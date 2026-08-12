from pathlib import Path


HTML = Path("ui/index.html").read_text(encoding="utf-8")


def test_dashboard_has_responsive_layout_guards():
    assert "@media (max-width: 900px)" in HTML
    assert "class=\"app-shell" in HTML
    assert "class=\"app-sidebar" in HTML
    assert "overflow-y: auto" in HTML


def test_dashboard_groups_primary_actions_and_overview():
    assert 'class="sidebar-actions' in HTML
    assert 'class="dashboard-overview' in HTML
    assert '<details id="discovery-report"' in HTML
    assert 'class="discovery-table-wrap' in HTML


def test_job_cards_and_modals_protect_narrow_layouts():
    assert 'class="job-card-header' in HTML
    assert 'class="job-card-actions' in HTML
    assert 'class="modal-panel' in HTML


def test_cover_letters_are_generated_on_demand_with_visible_states():
    assert 'onclick="openCL(this)"' in HTML
    assert 'id="cl-regenerate"' in HTML
    assert 'id="cl-generate"' in HTML
    assert 'No cover letter generated yet.' in HTML
    assert "method: 'GET'" in HTML
    assert 'Generating cover letter…' in HTML
    assert "method: 'POST'" in HTML
    assert "regenerate: regenerate" in HTML
    assert 'id="s4-icon"' not in HTML
