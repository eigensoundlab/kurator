# Security

Kurator runs locally and only listens on `127.0.0.1`. Please report security problems privately
through GitHub's **Report a vulnerability** (Security tab of
https://github.com/eigensoundlab/kurator), not in a public issue.

Keeping dependencies current:

- `update.bat` / `./update.sh` upgrade every dependency to its newest compatible version and run
  `pip-audit`.
- GitHub Actions runs `pip-audit` on every push and weekly; Dependabot proposes dependency updates.
