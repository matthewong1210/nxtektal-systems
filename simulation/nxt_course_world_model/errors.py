"""Fail-closed error taxonomy for the Course World Model."""

from __future__ import annotations


class CourseWorldModelError(ValueError):
    """A Course World Model contract violation.

    Raised when identity, coordinate-frame, geometry, elevation,
    serialization, digest, or revision rules are broken.  An invalid
    model is never usable: it satisfies no workflow prerequisite and
    answers no query.
    """


class CourseModelQueryError(CourseWorldModelError):
    """A malformed map query.

    Raised for non-finite or mistyped query inputs, invalid radii,
    malformed trajectories, and coordinate-frame mismatches.  A valid
    question with a negative answer (out of bounds, no containing hole,
    no terrain intersection) is reported through an explicit result
    status instead, never fabricated.
    """
