"""Pydantic request/response schemas.

Deliberately separate from the SQLAlchemy models: the wire format is allowed to
evolve independently of the storage schema, and no ORM object is ever returned
directly from a route.
"""
