"""Render an AuditReport into the self-contained HTML scorecard (the
billable one-page artifact, prints cleanly to PDF)."""

from __future__ import annotations

import os
from typing import Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import (AuditReport, STATUS_COLOR, STATUS_ICON, STATUS_LABEL)
from .layers import CHECK_LAYER, MODEL_TABLE, derive_layers
from .recommendations import build_recommendations, _headline

_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=select_autoescape(["html"]),
)


def _context(report: AuditReport) -> Dict:
    checks = []
    for c in report.checks:
        pct = 0 if c.fraction is None else round(c.fraction * 100)
        checks.append({
            "title": c.title,
            "summary": c.summary,
            "details": c.details,
            "remediation": c.remediation,
            "points": c.points,
            "weight": c.weight,
            "pct": pct,
            "status_label": STATUS_LABEL.get(c.status, c.status),
            "color": STATUS_COLOR.get(c.status, "#317fd2"),
            "icon": STATUS_ICON.get(c.status, "i"),
            "layer": CHECK_LAYER.get(c.id, ""),
        })

    # Two-layer read + model-family reference table.
    layers = derive_layers(report)
    for key in layers:
        st = layers[key]["status"]
        layers[key]["color"] = STATUS_COLOR.get(st, "#317fd2")
        layers[key]["label"] = STATUS_LABEL.get(st, st)
        layers[key]["icon"] = STATUS_ICON.get(st, "i")
    # SVG donut: circumference of r=52 is 2*pi*52 ≈ 326.7
    circumference = 2 * 3.14159 * 52
    dash = round(circumference * report.score / 100, 1)

    # Score-band colour for the gauge number + arc (3 bands, as requested):
    #   < 50 red · 50–65 amber · 66+ green.
    s = report.score
    score_color = "#16a34a" if s >= 66 else "#d97706" if s >= 50 else "#dc2626"
    return {
        "domain": report.domain,
        "ran_at": report.ran_at,
        "score": report.score,
        "grade": report.grade,
        "n_pass": report.n_pass,
        "n_warn": report.n_warn,
        "n_fail": report.n_fail,
        "checks": checks,
        "dash": dash,
        "score_color": score_color,
        "layers": layers,
        "model_table": MODEL_TABLE,
        "recommendations": build_recommendations(report),
        "rec_headline": _headline(report),
    }


def render_html(report: AuditReport) -> str:
    return _env.get_template("scorecard.html").render(**_context(report))
