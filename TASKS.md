# TASKS — iris-service-python

Open work only. Per `~/.claude/CLAUDE.md` rules : Python-only items
here ; done items removed (use `git tag -l` for history).

---

## 🚫 Blocked upstream

- **Docker image alpine** : 412 MB → ~280 MB possible. Blocked :
  `pydantic_core` / `cryptography` / `bcrypt` have no musl wheels.
  Revisit when uv ships musl wheels for these — track
  [astral-sh/uv issues](https://github.com/astral-sh/uv/issues).
