"""Runtime support layer (job runner, locks, trace logs, delegation store,
specialist store).

Lives at ``sweave.runtime`` so the web layer can import it without creating
a circular import through the existing ``sweave.tools`` / ``sweave.projects``
graph. Step 1 introduces only :mod:`sweave.runtime.locking`; the other modules
arrive in steps 4-6.
"""

from sweave.runtime.delegation_store import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_PREP,
    Delegation,
    DelegationStore,
    Manifest,
    PerProjectDelegationStores,
)
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.locking import (
    ProjectLockRegistry,
    atomic_write_json,
    atomic_write_json_sync,
)
from sweave.runtime.override_log import (
    OverrideLog,
    make_override_entry,
)
from sweave.runtime.specialist_store import (
    ORCHESTRATOR_NAME,
    ORCHESTRATOR_ROLE_REF,
    SCHEMA_VERSION as SPECIALIST_SCHEMA_VERSION,
    GlobalSpecialistStore,
    ProjectSpecialistStore,
    Specialist,
    SpecialistResolver,
)
from sweave.runtime.subagent_store import (
    MAX_RUNS,
    SubAgentRun,
    SubAgentRunStore,
    SubAgentPurpose,
    SubAgentStatus,
)
from sweave.runtime.trace_log import TraceLog, read_trace

__all__ = [
    "ProjectLockRegistry",
    "atomic_write_json",
    "atomic_write_json_sync",
    "TraceLog",
    "read_trace",
    "Delegation",
    "DelegationStore",
    "PerProjectDelegationStores",
    "Manifest",
    "JobRunner",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_PREP",
    "SubAgentRun",
    "SubAgentRunStore",
    "SubAgentPurpose",
    "SubAgentStatus",
    "MAX_RUNS",
    # M1.2
    "Specialist",
    "SpecialistResolver",
    "GlobalSpecialistStore",
    "ProjectSpecialistStore",
    "ORCHESTRATOR_NAME",
    "ORCHESTRATOR_ROLE_REF",
    "SPECIALIST_SCHEMA_VERSION",
    "OverrideLog",
    "make_override_entry",
]
