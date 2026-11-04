# Camera media commands in the WhatsApp bot

Add commands in **WhatsApp Bot Builder**, select the actions below, and save.
Existing command settings are preserved; these commands are not enabled automatically.

| Suggested trigger | Action | Example |
| --- | --- | --- |
| `/live` | Get live streaming link (admin) | `/live CAM-GATE-01` |
| `/snapgroup` | Latest snapshots by camera group | `/snapgroup Site A` |
| `/snaplocation` | Latest snapshots by location | `/snaplocation North Yard` |

Select a valid B-Snap API token in the builder. Both the token owner's group
and invoking whitelist number's group apply. An unset group means no group
restriction for that identity. Camera access uses the many-to-many memberships.
Group/location names match the full name, case-insensitively, including spaces;
SQL wildcard characters are treated literally. The live command accepts an exact
hostname, camera UUID, or IP address.

## Live links

Set `app_public_url` to the browser-accessible B-Snap address, enable Maps Live
View, and use an admin API token and whitelist admin sender. The link contains
no camera credentials or API token. Opening it requires a logged-in B-Snap admin
whose camera-group access is checked again. Sign into B-Snap before opening it.

The viewer uses the existing bounded, authenticated Maps Live endpoint. RTSP,
ONVIF discovery, and HLS use FFmpeg; the link API itself does not probe cameras.
An HTTP(S) snapshot URL instead refreshes its image every two seconds, supports
Basic/Digest authentication, and works without an IP field. This is periodic
image refresh; its motion rate depends on the snapshot endpoint. It does not
create saved snapshots. HTTP snapshot redirects are rejected. Multipart MJPEG
URLs are not single-image snapshot endpoints; use an RTSP/HLS source for video.

Streams retain the existing viewer capacity and three-minute duration limits;
reopen the link to start another preview. Camera status must be Active or
Standalone. Missing/unreachable sources return a safe error when opened.

Default response template:

```text
{{steps.result.body.camera}}
{{steps.result.body.url}}
B-Snap admin login required.
```

## Latest saved snapshots

One command sends one latest non-deleted saved snapshot per accessible camera,
including offline cameras. It does not capture new images. The bot automatically
requests all API pages (50 cameras per page), so it is not limited to the five
images used by the legacy hostname-prefix command. Large selections generate
multiple WhatsApp image messages. Existing chat routing, quoted replies, HD
delivery, audit preparation, and message-delivery monitoring are reused.

Cameras without a saved image, or whose latest file is missing, are reported in
a text message. Deleted snapshots are excluded. Files outside the snapshot root
are rejected. A missing latest file does not silently substitute an older image.
Failure after preparing some images sends those images with a partial-failure
notice. Transport failures use the existing media delivery failure notification.

The response template is rendered separately for each snapshot. Use response
type **Text**; the native action attaches the images automatically. Timestamp
formatting uses the configured timezone, including its timezone label.

```text
{{steps.result.body.camera}}
{{steps.result.body.timestamp|datetime}}
```

## APIs

- `GET /api/bot/cameras/live-link?keyword=CAM-GATE-01&phone_number=...`
  requires admin authentication and an admin whitelist entry. Returns `camera_id`,
  `camera`, `url`, and `requires_login`.
- `GET /api/bot/cameras/latest-snapshots?scope=group&keyword=Site%20A&phone_number=...`
  accepts authenticated viewer/operator/admin users and whitelisted senders.
  Use `scope=location` for location filtering. `page` starts at 1; `per_page` is
  1–50. Returns `results`, `missing`, `total_cameras`, `page`, and `has_more`.
  Each result includes `camera_id`, `camera`, `snapshot_id`, an ISO timestamp with
  UTC offset, and a protected snapshot `url`.

Both APIs require authentication even when a valid phone number is supplied.
Protected snapshot URLs retain their normal authentication and group checks.
Use the native actions for multi-image delivery; a generic API Flow image
response still represents a single selected image.
