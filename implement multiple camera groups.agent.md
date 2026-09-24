## Task: Support Multiple Camera Groups per Camera

Implement support for **one camera belonging to multiple Camera Groups**.

This feature will primarily be used by the existing **camera whitelist and notification system**.

### Current Limitation

Currently, a camera appears to support only one Camera Group.

The system needs to support the following relationship:

```text
Camera 1
├── Camera Group A
├── Camera Group B
└── Camera Group C
```

A single Camera Group can still contain multiple cameras.

Therefore, the relationship between `Camera` and `Camera Group` should effectively become **many-to-many** if it is not already implemented that way.

---

## Expected Behavior

Example:

```text
Camera: CCTV-GATE-01

Assigned Camera Groups:
- Security
- Data Center
```

Users assigned to the groups:

```text
Security
- security1@company.com
- security2@company.com

Data Center
- dco1@company.com
- dco2@company.com
```

If `CCTV-GATE-01` becomes **Offline**, the notification system must notify the users associated with **all Camera Groups assigned to that camera**.

Expected recipients:

```text
security1@company.com
security2@company.com
dco1@company.com
dco2@company.com
```

Do not stop processing after finding the first Camera Group.

The notification logic must resolve **all groups associated with the affected camera** and then resolve all users/recipients associated with those groups.

---

## Recipient Deduplication

Prevent accidental duplicate notifications.

For example:

```text
Security
- admin@company.com

Data Center
- admin@company.com
```

If the same email address/user belongs to multiple groups associated with the same camera event, the user should normally receive **only one notification for that camera event**.

Deduplicate recipients using an appropriate stable identifier, preferably:

1. User ID, if available.
2. Otherwise normalized email address.

Do not change this behavior if the existing application intentionally sends separate group-specific notifications. If that is the case, identify and document the existing behavior before modifying it.

---

## Important: Preserve Existing Email Notification Functionality

The existing email notification feature is already working and **must not be broken**.

Existing notification triggers, templates, SMTP configuration, retry behavior, background jobs, scheduler, queue, or notification service should continue working unless a change is strictly required for this feature.

Do not rewrite the email notification subsystem unnecessarily.

Prefer extending the current recipient-resolution logic.

Conceptually:

```text
Camera Offline
      │
      ▼
Find Camera
      │
      ▼
Find ALL Camera Groups
      │
      ▼
Find Users from ALL Groups
      │
      ▼
Deduplicate Recipients
      │
      ▼
Existing Email Notification Flow
```

---

## Whitelist Behavior

The whitelist logic must also support multiple Camera Groups.

Any part of the application that currently assumes:

```text
camera.group
camera.group_id
camera.camera_group
camera.camera_group_id
```

or equivalent one-to-one / many-to-one behavior must be reviewed.

Update the logic so it correctly handles a collection such as:

```text
camera.groups
```

or the equivalent relationship appropriate to the project's architecture.

Do not blindly rename fields. Follow the conventions and ORM/data-access patterns already used by the project.

---

## Database / Data Model

First inspect the existing schema and ORM models.

If the current schema stores something equivalent to:

```text
camera.camera_group_id
```

consider implementing a proper join/pivot table, for example:

```text
cameras
camera_groups
camera_camera_groups
```

Conceptually:

```text
camera_camera_groups

camera_id
camera_group_id
```

Use the naming conventions already established by the project.

Requirements:

* One camera can belong to many groups.
* One group can contain many cameras.
* Prevent duplicate camera/group relationships.
* Add appropriate foreign keys.
* Add appropriate indexes.
* Preserve referential integrity.
* Do not delete existing camera/group assignments during migration.

If database migration is necessary, make it safe for existing production data.

Existing:

```text
Camera A -> Group 1
Camera B -> Group 2
```

must still produce:

```text
Camera A -> [Group 1]
Camera B -> [Group 2]
```

after migration.

---

## Backward Compatibility

This change must not break existing cameras that currently have only one Camera Group.

These cases must continue to work:

```text
Camera with no group
Camera with one group
Camera with multiple groups
```

Existing APIs, UI pages, monitoring processes, whitelist behavior, and email notification functionality should remain compatible wherever reasonably possible.

If an API contract needs to change, prefer an additive change rather than removing an existing field immediately.

---

## Backend

Review all relevant backend code, including:

* Camera model/entity.
* Camera Group model/entity.
* Database queries.
* Camera create endpoint.
* Camera update endpoint.
* Camera detail endpoint.
* Camera list endpoint.
* Camera Group endpoints.
* Whitelist logic.
* Offline/online detection logic.
* Notification recipient resolver.
* Email notification service.
* Scheduled/background monitoring jobs.
* Serialization / DTO / API response models.
* Validation.
* Delete/update relationship handling.

Search the codebase for every assumption that a camera has exactly one Camera Group.

Do not modify only the database model while leaving business logic dependent on a single `group_id`.

---

## Frontend / UI

If the application contains a UI for assigning Camera Groups, update it so that users can select **more than one Camera Group for a camera**.

Prefer an existing project component such as:

```text
multi-select
checkbox group
tag selector
```

rather than introducing a new UI library.

Example:

```text
Camera Groups

[x] Security
[x] Data Center
[ ] Office
[ ] Mining Area
```

When editing an existing camera, its currently assigned groups must be selected correctly.

The UI must support:

* Assigning one group.
* Assigning multiple groups.
* Removing one group without removing others.
* Removing all groups if permitted by existing business rules.
* Loading previously assigned groups.
* Saving changes correctly.

---

## API Behavior

Prefer a structure similar to:

```json
{
  "id": 123,
  "name": "CCTV-GATE-01",
  "camera_groups": [
    {
      "id": 1,
      "name": "Security"
    },
    {
      "id": 5,
      "name": "Data Center"
    }
  ]
}
```

For create/update operations, an ID-array style payload is acceptable:

```json
{
  "name": "CCTV-GATE-01",
  "camera_group_ids": [1, 5]
}
```

However, use the project's existing API conventions instead of forcing these exact names.

Validate:

* Group IDs exist.
* Duplicate IDs are handled safely.
* Unauthorized group assignment is rejected if authorization rules exist.
* Empty group lists are handled according to existing business requirements.

---

## Email Notification Test Scenarios

At minimum verify these cases.

### Scenario 1 — One Camera, One Group

```text
Camera A
└── Group A
    ├── User 1
    └── User 2
```

Camera A Offline.

Expected:

```text
User 1 receives notification
User 2 receives notification
```

---

### Scenario 2 — One Camera, Two Groups

```text
Camera A
├── Group A
│   ├── User 1
│   └── User 2
│
└── Group B
    ├── User 3
    └── User 4
```

Camera A Offline.

Expected:

```text
User 1 notified
User 2 notified
User 3 notified
User 4 notified
```

---

### Scenario 3 — Same User in Multiple Groups

```text
Camera A
├── Group A
│   └── User 1
│
└── Group B
    └── User 1
```

Camera A Offline.

Expected:

```text
User 1 receives only one notification
```

unless the current system explicitly requires per-group notifications.

---

### Scenario 4 — Camera Without Group

```text
Camera A
└── No Camera Group
```

The monitoring process must not crash.

Handle this case consistently with the application's current behavior.

---

### Scenario 5 — Multiple Cameras

```text
Camera A -> Group A + Group B
Camera B -> Group B
Camera C -> Group C
```

An Offline event for Camera A must not notify users from Group C.

Recipients must only come from groups actually associated with Camera A.

---

## Online / Recovery Notifications

If the existing system also sends notifications when a camera returns to **Online/Recovered**, the same multi-group recipient resolution must apply.

Example:

```text
Camera A:
Offline -> users in Group A + Group B notified

Camera A:
Online/Recovered -> users in Group A + Group B notified
```

Do not implement this if the existing system does not currently send recovery notifications.

---

## Concurrency and Duplicate Event Protection

Review how camera state changes are detected.

Do not introduce duplicate emails because the monitoring job executes repeatedly while the camera remains offline.

For example:

```text
10:00 Camera Offline
10:01 Monitoring runs
10:02 Monitoring runs
10:03 Monitoring runs
```

The change from one group to multiple groups must not accidentally cause the existing duplicate-event protection to stop working.

Preserve the application's current event/state-change behavior.

---

## Implementation Principles

Before modifying code:

1. Inspect the repository structure.
2. Identify the technology stack and ORM.
3. Locate Camera and Camera Group models.
4. Locate the existing database relationship.
5. Locate whitelist logic.
6. Trace the full offline notification flow.
7. Identify where notification recipients are currently obtained.
8. Search for every usage of the existing single Camera Group relationship.
9. Determine the smallest safe architectural change.

Do not assume filenames, table names, framework, ORM, or architecture before inspecting the repository.

---

## Engineering Constraints

Follow these principles during implementation:

* Make the smallest coherent change.
* Follow existing project architecture and naming conventions.
* Avoid unrelated refactoring.
* Avoid introducing unnecessary dependencies.
* Do not rewrite working functionality without a clear reason.
* Maintain backward compatibility.
* Avoid N+1 database queries.
* Use eager loading / joins / batching where appropriate.
* Maintain transactional integrity when updating relationships.
* Validate all input.
* Preserve existing authorization rules.
* Preserve existing notification templates.
* Preserve existing logging.
* Preserve existing monitoring behavior.
* Handle empty/null relationships safely.
* Add useful logs for notification recipient resolution if consistent with the project.
* Never silently swallow notification errors unless the existing architecture intentionally does so.

---

## Testing

Add or update automated tests according to the project's existing testing framework.

At minimum test:

```text
[ ] Camera can have one group.
[ ] Camera can have multiple groups.
[ ] Camera group can contain multiple cameras.
[ ] Duplicate camera-group association is prevented.
[ ] Existing single-group camera data remains valid.
[ ] Camera create supports multiple groups.
[ ] Camera update supports multiple groups.
[ ] Removing one group doesn't remove other groups.
[ ] Whitelist recognizes all groups.
[ ] Offline notification resolves users from every camera group.
[ ] Duplicate recipient is handled correctly.
[ ] User from unrelated group does not receive email.
[ ] Camera without group does not crash notification processing.
[ ] Existing email functionality remains operational.
[ ] Recovery notification continues working if currently supported.
```

Run relevant tests after implementation.

If the repository does not currently have automated tests for this area, perform targeted verification and clearly state what was manually verified.

---

## Migration Safety

If a database migration is required:

1. Create the new relationship structure.
2. Migrate existing camera/group relationships.
3. Verify migrated row counts.
4. Ensure application code can read the new relationship.
5. Avoid destructive schema changes until the new implementation is working.
6. Do not discard existing production relationships.

If removing an old `camera_group_id` column is unnecessary for this task, prefer leaving it temporarily or following the project's established migration strategy rather than performing a risky destructive migration.

---

## Definition of Done

The task is complete only when:

```text
✓ One camera can be assigned to multiple Camera Groups.

✓ Existing cameras with one Camera Group continue working.

✓ Camera whitelist logic supports multiple Camera Groups.

✓ When a camera becomes Offline, users belonging to ALL associated
  Camera Groups are included as notification recipients.

✓ Users from unrelated Camera Groups are not notified.

✓ Duplicate recipient notifications are avoided where appropriate.

✓ Existing email notification functionality remains operational.

✓ Existing camera monitoring behavior remains operational.

✓ Database migration preserves existing assignments.

✓ Frontend/API can create and update multiple Camera Group assignments.

✓ Relevant tests pass.
```

---

## Required Work Process

Do not only provide an implementation proposal.

Inspect the actual repository and **implement the feature**.

Proceed in this order:

```text
1. Analyze existing implementation.
2. Trace Camera -> Camera Group -> User -> Notification flow.
3. Identify all single-group assumptions.
4. Design the smallest safe change.
5. Implement database/model changes if necessary.
6. Update backend/API logic.
7. Update whitelist logic.
8. Update frontend assignment UI if present.
9. Update notification recipient resolution.
10. Add/update tests.
11. Run tests and relevant validation.
12. Review the diff for regressions.
```

Before finishing, inspect the final diff specifically for:

```text
- accidental unrelated changes
- notification regression
- broken existing API behavior
- incorrect database migration
- N+1 queries
- duplicated recipients
- null relationship errors
- code paths that still assume a single Camera Group
```

At the end, provide a concise implementation report containing:

### Changes Made

List files/components changed and why.

### Database Changes

Explain schema/migration changes and compatibility with existing data.

### Notification Flow

Explain how recipients are now resolved when a camera belongs to multiple groups.

### Tests Performed

List automated/manual tests executed and their results.

### Risks / Notes

Mention any remaining compatibility concern or recommended follow-up.

Do not claim a test passed unless it was actually executed.
