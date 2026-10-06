"""Run with python -m unittest app.tests.test_wa_api_methods -v."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request

from app.utils.wa_bot_workflow import APIFlowError, normalize_flow, render_flow_template, run_api_flow
from app.utils.wa_bot_workflow import bind_command_arguments, render_flow_value


class WorkflowMethodTests(unittest.IsolatedAsyncioTestCase):
    def test_named_inputs_are_available_before_api_execution(self) -> None:
        params = bind_command_arguments(["hostname", "duration"], "CCTV-GATE-01 30")
        context = {"params": params, "steps": {}}
        self.assertEqual(render_flow_template(
            "Recording {{params.hostname}} for {{duration}}s", context
        ), "Recording CCTV-GATE-01 for 30s")
        self.assertEqual(render_flow_value({
            "hostname": "{{params.hostname}}", "duration": "{{duration}}",
        }, context), {"hostname": "CCTV-GATE-01", "duration": "30"})
        self.assertEqual(bind_command_arguments(["hostname"], "Camera Gate One"), {"hostname": "Camera Gate One"})
        self.assertEqual(bind_command_arguments(["hostname", "note"], "gate Camera Gate One"), {"hostname": "gate", "note": "Camera Gate One"})
        self.assertEqual(bind_command_arguments([], "gate"), {})
    def test_legacy_nodes_default_to_get(self) -> None:
        node = normalize_flow([{"name": "data", "path": "/data"}])[0]
        self.assertEqual(node["method"], "GET")
        self.assertEqual(node["body"], {})

    def test_missing_named_arguments_report_how_to_fill_inputs(self) -> None:
        for names, argument in ((["hostname"], ""), (["hostname", "duration"], "camera-a")):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "Fill Test argument"):
                bind_command_arguments(names, argument)
        with self.assertRaisesRegex(ValueError, "Add 'hostname' to Required parameters"):
            render_flow_value({"hostname": "{{params.hostname}}"}, {"params": {}})

    def test_post_json_is_preserved_and_validated(self) -> None:
        node = normalize_flow([{
            "name": "data", "path": "/api/videos/record", "method": "post",
            "body": '{"hostname":"{{argument}}","duration":30}',
        }])[0]
        self.assertEqual(node["method"], "POST")
        self.assertEqual(node["body"]["duration"], 30)
        for fields in ({"method": "DELETE"}, {"body": "invalid"}, {"body": []}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                normalize_flow([{"name": "data", "path": "/data", **fields}])

    async def test_get_then_post_renders_body_and_sends_bearer_token(self) -> None:
        app = FastAPI()
        requests = []

        @app.get("/lookup")
        async def lookup() -> dict:
            return {"hostname": "CCTV-GATE-01"}

        @app.post("/api/videos/record", status_code=202)
        async def record(request: Request) -> dict:
            body = await request.json()
            requests.append((body, request.headers["authorization"]))
            return {"message": "Recording queued", **body}

        flow = normalize_flow([
            {"name": "lookup", "path": "/lookup"},
            {
                "name": "data", "path": "/api/videos/record", "method": "POST",
                "body": {"hostname": "{{steps.lookup.body.hostname}}", "duration": 30},
            },
        ])
        with patch.dict(sys.modules, {"app.main": SimpleNamespace(app=app)}):
            context = await run_api_flow(
                flow=flow, bearer_token="test-token", sender="sender", argument="camera"
            )
        self.assertEqual(context["steps"]["data"]["status_code"], 202)
        self.assertEqual(requests, [({"hostname": "CCTV-GATE-01", "duration": 30}, "Bearer test-token")])

    async def test_duplicate_ip_error_stops_following_nodes(self) -> None:
        app = FastAPI()
        following_requests = []

        @app.post("/api/videos/record")
        async def duplicate() -> None:
            raise HTTPException(status_code=409, detail="Duplicate IP. Recording was not started.")

        @app.get("/following")
        async def following() -> dict:
            following_requests.append(True)
            return {}

        flow = normalize_flow([
            {"name": "data", "path": "/api/videos/record", "method": "POST", "body": {"ip": "{{argument}}"}},
            {"name": "following", "path": "/following"},
        ])
        with patch.dict(sys.modules, {"app.main": SimpleNamespace(app=app)}):
            with self.assertRaisesRegex(ValueError, "HTTP 409.*Duplicate IP"):
                await run_api_flow(
                    flow=flow, bearer_token="test-token", sender="sender", argument="192.168.1.100"
                )
        self.assertEqual(following_requests, [])

    async def test_http_error_retains_failed_and_previous_node_variables(self) -> None:
        for status in (404, 403, 422):
            with self.subTest(status=status):
                app = FastAPI()

                @app.get("/lookup")
                async def lookup() -> dict:
                    return {"hostname": "camera-a"}

                @app.get("/failed")
                async def failed() -> None:
                    raise HTTPException(status_code=status, detail="Camera not found")

                flow = normalize_flow([
                    {"name": "lookup", "path": "/lookup"},
                    {"name": "data", "path": "/failed"},
                    {"name": "following", "path": "/lookup"},
                ])
                with patch.dict(sys.modules, {"app.main": SimpleNamespace(app=app)}):
                    with self.assertRaises(APIFlowError) as raised:
                        await run_api_flow(flow=flow, bearer_token=None, sender="sender", argument="camera-a")
                context = raised.exception.context
                self.assertEqual(context["steps"]["data"]["status_code"], status)
                self.assertEqual(context["steps"]["data"]["body"]["detail"], "Camera not found")
                self.assertNotIn("following", context["steps"])
                self.assertEqual(render_flow_template(
                    "{{steps.lookup.body.hostname}}: HTTP {{steps.data.status_code}} {{steps.data.body.detail}}", context
                ), f"camera-a: HTTP {status} Camera not found")


if __name__ == "__main__":
    unittest.main()
