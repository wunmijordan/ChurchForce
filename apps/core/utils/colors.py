import hashlib

# ---------------------------------------------------
# MASTER COLOR PALETTE (Tabler-compatible)
# ---------------------------------------------------

COLOR_PAIRS = [
    ("bg-blue-lt text-white", "#3b82f6"),
    ("bg-green-lt text-white", "#22c55e"),
    ("bg-orange-lt text-white", "#fb923c"),
    ("bg-purple-lt text-white", "#8b5cf6"),
    ("bg-pink-lt text-white", "#ec4899"),
    ("bg-cyan-lt text-white", "#06b6d4"),
    ("bg-yellow-lt text-white", "#eab308"),
    ("bg-red-lt text-white", "#ef4444"),
    ("bg-indigo-lt text-white", "#6366f1"),
    ("bg-teal-lt text-white", "#14b8a6"),
    ("bg-lime-lt text-white", "#84cc16"),
    ("bg-amber-lt text-white", "#f59e0b"),
    ("bg-fuchsia-lt text-white", "#d946ef"),
    ("bg-emerald-lt text-white", "#10b981"),
    ("bg-violet-lt text-white", "#7c3aed"),
    ("bg-rose-lt text-white", "#f43f5e"),
    ("bg-sky-lt text-white", "#0ea5e9"),
    ("bg-blue text-white", "#2563eb"),
    ("bg-green text-white", "#16a34a"),
    ("bg-orange text-white", "#ea580c"),
    ("bg-purple text-white", "#9333ea"),
    ("bg-pink text-white", "#db2777"),
    ("bg-cyan text-white", "#0891b2"),
    ("bg-yellow text-white", "#ca8a04"),
    ("bg-red text-white", "#dc2626"),
    ("bg-indigo text-white", "#4f46e5"),
    ("bg-teal text-white", "#0d9488"),
    ("bg-lime text-white", "#65a30d"),
    ("bg-amber text-white", "#d97706"),
    ("bg-emerald text-white", "#059669"),
    ("bg-slate text-white", "#334155"),
    ("bg-gray text-white", "#4b5563"),
]

DEFAULT_COLOR = ("bg-dark text-white", "#0f172a")

def _hash_index(seed: str) -> int:
    """
    Stable deterministic index generator.
    Same input ALWAYS returns same color.
    """
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest, 16) % len(COLOR_PAIRS)

def resolve_color(seed=None, variant="class"):
    """
    Universal color resolver.

    variant:
        - 'class'
        - 'hex'
        - 'both'
    """

    if not seed:
        color_class, hex_color = DEFAULT_COLOR
    else:
        index = _hash_index(str(seed))
        color_class, hex_color = COLOR_PAIRS[index]

    if variant == "hex":
        return hex_color

    if variant == "both":
        return {
            "class": color_class,
            "hex": hex_color,
        }

    return color_class

def get_member_color(member_id, variant="class"):
    """
    Stable user avatar color.
    """
    return resolve_color(f"user:{member_id}", variant)

def get_unit_color(unit_id=None, unit_name=None, variant="class"):
    """
    Deterministic unit color.
    """
    seed = unit_id or unit_name
    return resolve_color(f"unit:{seed}", variant)