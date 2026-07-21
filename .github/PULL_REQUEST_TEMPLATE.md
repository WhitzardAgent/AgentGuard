## Summary

<!-- What does this PR do, and why? Link related issues, e.g. "Closes #123". -->

## Type of Change

- [ ] Bug fix
- [ ] New feature (framework adapter, plugin, policy capability, ...)
- [ ] Breaking change
- [ ] Documentation
- [ ] CI / tooling / chore

## Changes

<!-- Bullet list of the notable changes in this PR. -->

-

## How Has This Been Tested?

<!-- Commands you ran, new/updated tests, manual verification steps. -->

```bash
ruff check src tests
pytest -q
node --test
```

## Checklist

- [ ] I read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] I added/updated tests that cover this change (especially for rule/policy
      engine or client enforcement path changes).
- [ ] I updated relevant docs (`docs/en/**`, `docs/zh/**`, or `README.md` /
      `README_CN.md`) for user-facing changes.
- [ ] `ruff check src tests` passes locally.
- [ ] `pytest -q` and `node --test` pass locally.
- [ ] This PR does **not** introduce a policy-bypass risk (an action that
      should be denied could now be allowed). If it touches decision logic,
      I added a regression test for the previous behavior.

## Additional Context

<!-- Screenshots, benchmarks, migration notes, or anything else reviewers should know. -->
