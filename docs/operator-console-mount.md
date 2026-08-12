# Operator console mount contract

The consolidated console is a FastAPI plugin. It uses the host application's
existing operator and executor authentication dependencies and the one injected
`PlanAuthority` store. It does not import `tgw.http_server` or create another
authority backend.

Construct `OperatorConsoleMount` during application composition, then the exact
host mount is one line:

```python
mount_operator_console(app, console_config)
```

The hook refuses duplicate mounts and route collisions instead of silently
shadowing an older approval page. After mounting, shared navigation metadata is
at `/api/operator-console/discovery`; web and Flutter consume the same JSON at
`/api/operator-console/requests`.
