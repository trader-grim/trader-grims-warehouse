# Plan Vault relocated

The TGW Plan Vault is a standalone Git repository at:

`/opt/TGW/library/plans`

Do not restore or edit a repository-local Plan copy. Runtime consumers obtain
the Plan root from `plan_vault_path`; its default is the standalone repository.
Historical application commits retain the former embedded copy for recovery.

