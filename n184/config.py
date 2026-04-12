"""Configuration and constants for the N184 Memory Palace."""

from pathlib import Path

# Default storage location
N184_HOME = Path.home() / ".n184"
SQLITE_DB_PATH = N184_HOME / "memory_palace.db"
CHROMADB_PATH = N184_HOME / "memory_palace_chromadb"

# The Seven Halls - ChromaDB collection definitions
HALLS = {
    "vulnerabilities": {
        "collection": "hall_vulnerabilities",
        "description": "CVEs, exploits, and confirmed attack patterns",
    },
    "bugs": {
        "collection": "hall_bugs",
        "description": "Non-exploitable defects: crashes, leaks, logic errors",
    },
    "advocatus_diaboli": {
        "collection": "hall_advocatus_diaboli",
        "description": "HIL lessons learned, Dan <-> Honore dialogue",
    },
    "avocado_smash": {
        "collection": "hall_avocado_smash",
        "description": "De-securitization tactics for resistant maintainers",
    },
    "culture": {
        "collection": "hall_culture",
        "description": "Project-specific communication patterns",
    },
    "git_archaeology": {
        "collection": "hall_git_archaeology",
        "description": "Historical bug-fix patterns from commit history",
    },
    "documentation": {
        "collection": "hall_documentation",
        "description": "Spec contradictions, undocumented behavior",
    },
}

# Severity levels
SEVERITIES = ("critical", "high", "medium", "low", "info")

# Verbosity levels for culture profiles
VERBOSITY_LEVELS = ("minimal", "moderate", "verbose")

# Formality levels
FORMALITY_LEVELS = ("casual", "professional", "academic")

# Security framing options
SECURITY_FRAMING = ("avoid", "moderate", "required")

# Feedback types
FEEDBACK_TYPES = ("confirmed", "false_positive", "needs_context", "reframe")
