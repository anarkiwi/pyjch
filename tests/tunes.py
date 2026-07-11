"""HVSC relpaths of the JCH NewPlayer reference tunes the suite exercises.

The base pysidtracker `hvsc` fixture resolves/caches these; this module is just
the list of relpaths (test data), replacing the old conftest/fetch_tunes cache
manager.
"""

# The canonical byte-exact V0x reference tunes (load $1000, init $1060, play $10E8).
V0X_REFERENCES = [
    "MUSICIANS/S/Scorpio/Flexible.sid",
    "MUSICIANS/J/JCH/Simple_Tune.sid",
]
