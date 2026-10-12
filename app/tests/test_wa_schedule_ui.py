"""Execute schedule serialization and field behavior with an isolated DOM stub."""

from __future__ import annotations

import shutil
import subprocess
import tempfile

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """These frontend checks do not create database tables."""


def test_multiple_schedule_editor_serializes_stable_ids_and_localizes_labels() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the frontend check")
    source = (Path(__file__).resolve().parents[2] / "static/js/wa-bot-builder.js").read_text(
        encoding="utf-8"
    )
    functions = source[
        source.index("  function collectCommand()") : source.index("  function addScheduleRow(")
    ]
    script = (
        r"""
const assert = require('node:assert/strict');
const document = {documentElement:{lang:'en'}};
const t = (en,id) => document.documentElement.lang === 'id' ? id : en;
const adminActions = [], privateOnlyActions = [];
const draft = {id:'custom_test', schedule:{enabled:true}};
const elements = {};
for (const key of ['name','trigger','description','response','action','chat_scope','response_type','response_image_source','response_video_source','aliases','required_params','processing_delay_seconds']) elements[key] = {value:''};
elements.trigger.value = '/snap'; elements.action.value = 'snap';
elements.enabled = {checked:true}; elements.quote_reply = {checked:true};
function makeRow(id,mode,argument,recipient) {
  const fields = {};
  for (const [key,value] of Object.entries({mode,argument,recipient,time:'08:00',cron:'30 18 * * *'})) fields[key] = {value};
  fields.enabled = {checked:true};
  const label = {textContent:''};
  const containers = {time:{classList:{toggle() {}}},cron:{classList:{toggle() {}}}};
  return {dataset:{scheduleId:id},fields,label,querySelector(selector) {
    if (selector === '[data-schedule-label]') return label;
    if (selector.includes('data-schedule-')) return containers[selector.includes('time') ? 'time':'cron'];
    return fields[selector.match(/schedule_([a-z]+)/)[1]];
  }};
}
const rows = [makeRow('morning','daily','CAM-01','628111111111'),makeRow('evening','cron','CAM-02','628222222222')];
const form = {elements,querySelectorAll() {return rows;}};
"""
        + functions
        + r"""
updateScheduleFields();
assert.equal(rows[0].fields.time.required,true);
assert.equal(rows[0].fields.cron.required,false);
assert.equal(rows[1].fields.cron.required,true);
assert.equal(rows[1].fields.time.required,false);
let collected = collectCommand();
assert.deepEqual(collected.schedules.map(s=>[s.id,s.argument,s.recipient]),[
  ['morning','CAM-01','628111111111'],['evening','CAM-02','628222222222']]);
assert.equal('schedule' in collected,false);
rows[1].fields.enabled.checked = false; updateScheduleFields();
assert.equal(rows[1].fields.recipient.required,false);
assert.equal(rows[1].fields.cron.required,false);
rows.reverse();
assert.equal(collectCommand().schedules[0].id,'evening');
refreshScheduleLabels(); assert.equal(rows[0].label.textContent,'Schedule 1');
document.documentElement.lang='id'; refreshScheduleLabels(); assert.equal(rows[0].label.textContent,'Jadwal 1');
document.documentElement.lang='en'; refreshScheduleLabels(); assert.equal(rows[0].label.textContent,'Schedule 1');
rows.pop(); assert.equal(collectCommand().schedules.length,1);
"""
    )
    with tempfile.TemporaryDirectory() as directory:
        script_path = Path(directory) / "schedule-check.js"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [node, str(script_path)], capture_output=True, text=True, check=False
        )
    assert result.returncode == 0, result.stderr
