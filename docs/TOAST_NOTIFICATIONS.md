# Toast Notification System

B-Snap includes a powerful toast notification system for real-time user feedback via WebSocket.

## Features

- 🔔 **Real-time notifications** via WebSocket
- 🎨 **4 notification types**: Success, Error, Warning, Info
- 📍 **6 positions**: Top/bottom + left/center/right
- 🔗 **Action buttons** in notifications
- 🌙 **Dark mode support**
- 📱 **Responsive design**
- ⏱️ **Auto-dismiss** with progress bar
- 🔒 **Persistent notifications** (require manual close)

## Quick Start

### Server-Side (Python)

```python
from app.utils.notification_service import NotificationService

# Simple notifications
await NotificationService.success("Snapshot saved!")
await NotificationService.error("Camera is offline")
await NotificationService.warning("Storage is 90% full")
await NotificationService.info("Scheduled task completed")

# With options
await NotificationService.send_toast(
    message="Custom notification",
    title="Custom Title",
    toast_type=ToastType.SUCCESS,
    duration=5000,  # milliseconds
    position=ToastPosition.TOP_RIGHT,
    dismissible=True,
    actions=[
        {"label": "View", "url": "/snapshots/123"},
        {"label": "Dismiss", "url": "#"},
    ],
)
```

### Client-Side (JavaScript)

```javascript
// Simple notifications
Toast.success('Operation completed!');
Toast.error('Something went wrong');
Toast.warning('Please check settings');
Toast.info('New update available');

// With options
Toast.success('Snapshot saved', {
    duration: 5000,
    position: 'top-center',
    actions: [
        { label: 'View', url: '/snapshots/123' }
    ]
});

// Control
Toast.dismiss(toastId);      // Dismiss specific toast
Toast.dismissAll();          // Dismiss all toasts
```

## Notification Types

| Type | Icon | Use Case |
|------|------|----------|
| `success` | ✅ Green | Operation completed successfully |
| `error` | ❌ Red | Operation failed, errors |
| `warning` | ⚠️ Yellow | Warnings, attention needed |
| `info` | ℹ️ Blue | Informational messages |

## Positions

```python
ToastPosition.TOP_RIGHT      # Default
ToastPosition.TOP_LEFT
ToastPosition.TOP_CENTER
ToastPosition.BOTTOM_RIGHT
ToastPosition.BOTTOM_LEFT
ToastPosition.BOTTOM_CENTER
```

## Pre-built System Notifications

```python
# Camera events
await NotificationService.camera_offline(camera_name="Front Gate", camera_id=1)
await NotificationService.camera_online(camera_name="Front Gate", camera_id=1)

# Snapshot events
await NotificationService.snapshot_saved(
    camera_name="Front Gate",
    camera_id=1,
    snapshot_id=123
)

```

## API Endpoints

### Get Notifications
```http
GET /api/notifications/?unread_only=true&limit=50
```

### Get Unread Count
```http
GET /api/notifications/unread-count
```

### Mark as Read
```http
POST /api/notifications/{id}/read
```

### Mark All as Read
```http
POST /api/notifications/mark-all-read
```

### Delete Notification
```http
DELETE /api/notifications/{id}
```

## Demo Page

Visit `/demo/toast/` to see all notification types in action.

## Integration Examples

### In a Route Handler

```python
from app.utils.notification_service import NotificationService

@router.post("/cameras/{camera_id}/snapshot")
async def take_snapshot(camera_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        # ... capture logic ...
        
        await NotificationService.snapshot_saved(
            camera_name=camera.name,
            camera_id=camera_id,
            snapshot_id=snapshot.id
        )
        return {"success": True}
        
    except Exception as e:
        await NotificationService.error(f"Failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

### In Background Jobs

```python
from app.utils.notification_service import NotificationService

async def scheduled_snapshot():
    cameras = get_active_cameras()
    
    for camera in cameras:
        try:
            await capture_snapshot(camera)
            await NotificationService.snapshot_saved(
                camera_name=camera.name,
                camera_id=camera.id,
                snapshot_id=snapshot.id
            )
        except Exception as e:
            await NotificationService.camera_offline(
                camera_name=camera.name,
                camera_id=camera.id
            )
```

## Database Schema

Notifications can be persisted to the database:

```python
# Save to database
await NotificationService.send_toast(
    message="Important message",
    db=db,  # Pass database session
    persistent=True
)
```

Table: `notifications`
- `id`: Primary key
- `title`: Notification title
- `message`: Notification content
- `type`: success/error/warning/info
- `user_id`: Target user (null = broadcast)
- `camera_id`: Associated camera
- `is_read`: Read timestamp
- `created_at`: Creation timestamp

## Configuration

Environment variables (optional):
```env
# Default toast duration (milliseconds)
DEFAULT_TOAST_DURATION=5000

# Max concurrent toasts
MAX_TOASTS=5

# WebSocket reconnect interval (seconds)
WS_RECONNECT_INTERVAL=5
```

## Custom Styling

The toast system uses Tailwind CSS classes. Override in your CSS:

```css
/* Custom toast styles */
.toast-item {
    /* Your custom styles */
}

.toast-progress {
    /* Progress bar styles */
}
```

## Browser Support

- Chrome/Edge 80+
- Firefox 75+
- Safari 13+
- All modern mobile browsers

## Troubleshooting

### WebSocket not connecting
1. Check browser console for errors
2. Verify WebSocket URL in `toast.js`
3. Ensure firewall allows WebSocket connections

### Notifications not appearing
1. Check if `toast.js` is loaded: `window.Toast` should exist
2. Verify WebSocket connection status
3. Check browser notification permissions

### Database errors
1. Run migrations: `alembic upgrade head`
2. Check database connection
3. Verify `notifications` table exists
