# Linked demonstration accounts

`tools/provision_demo_accounts.py` creates an explicitly requested batch of three
sales team leaders and five reseller accounts from a private JSON manifest.
Each reseller references one of the new leaders by email. Demo emails use the
reserved `@meattrack.test` domain. The tool sends no email and changes no existing
account passwords, orders, inventory, or portal settings.

Back up the database before committing or applying a batch. Verify the custom
archive with `pg_restore --list`, retain the current Git commit or bundle, and
record that backup in the provisioning command:

```bash
python tools/provision_demo_accounts.py \
  --manifest /private/demo-manifest.json \
  --backup-reference /backups/pre-demo-accounts.dump \
  --report /private/demo-created.json
```

The manifest contains `batch_key`, `sales_leaders`, and `resellers`. Every account
needs `name`, `email`, and its own `password`. Each reseller also needs
`business_name`, `contact_number`, `address`, and `leader_email`. Keep manifests,
passwords, and output reports outside source control; remove temporary production
copies of the manifest after verification.

The batch is created in one database transaction with an owner-attributed audit
record for every account and assignment. A collision or late insertion failure
rolls back the entire batch. Rerunning the same batch verifies its identities and
links without resetting passwords or duplicating records. A changed assignment or
unrelated existing email causes the operation to stop.

Verify all eight logins and the five reseller links after applying the batch.
Demo addresses cannot receive email; use the deployment's existing login policy
and do not weaken authentication settings for this tool.

## Recovery

An insertion failure needs no database restore because the transaction rolls back.
Keep the private creation report to identify only the new accounts if they later
need to be disabled. Existing transactions and accounts can then be retained.

For a full recovery to the backup time, stop app and worker writes, preserve a
current emergency backup, and restore the verified pre-change archive with
`pg_restore --clean --if-exists --single-transaction --exit-on-error` against the
same database. Check out the recorded pre-change Git commit and restart app and
worker. A full restore returns all database records to the backup time, so use it
before allowing new production writes. The backups and private creation reports
are operational artifacts and are never included in Git or an application image.
