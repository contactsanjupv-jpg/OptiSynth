"""
Typed representations of database rows. There is no ORM in this MVP (see
README "Migrating to PostgreSQL" for the SQLAlchemy migration path) --
repositories return plain dicts built from sqlite3.Row objects. These
dataclasses exist purely as documentation/type-hints for what a "Project"
etc. dict is expected to contain; they are not used for validation (see
backend/app/schemas/ for that).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Organization:
    id: int
    name: str
    created_at: str


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    display_name: Optional[str]
    is_active: bool
    created_at: str


@dataclass
class Membership:
    id: int
    user_id: int
    organization_id: int
    role: str  # 'owner' | 'admin' | 'member'
    created_at: str


@dataclass
class Project:
    id: int
    organization_id: int
    created_by_user_id: Optional[int]
    name: str
    objective: Optional[str]
    target_metric: str
    direction: str  # 'maximize' | 'minimize'
    feature_columns: list
    constraints: list
    target_value: Optional[float]
    status: str  # 'draft' | 'running' | 'completed'
    created_at: str
    updated_at: str


@dataclass
class Experiment:
    id: int
    organization_id: int
    project_id: int
    dataset_id: Optional[int]
    features: dict
    target_value: float
    constraint_values: dict
    source: str  # 'historical' | 'recommended'
    created_at: str
