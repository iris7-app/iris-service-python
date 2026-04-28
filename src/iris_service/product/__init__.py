"""Product domain — JPA-equivalent ORM + Pydantic DTOs + FastAPI router.

Mirrors the Java sibling `com.mirador.product.*` package : same schema
(see alembic 0002 migration), same DTOs (Bean Validation ⇄ Pydantic),
same minimal CRUD endpoints (GET list / get / POST / DELETE — PUT is
deferred to a follow-up MR per the foundation-MR-first convention).
"""
