"""Form builder and version history / restore."""
import io

import pytest
from pypdf import PdfReader

import pdf_corpus as corpus
from docintel.pdf import formbuilder as FB
from docintel.pdf import forms
from docintel.pdf.engine import PDFEngineError


def specs():
    return [
        FB.FieldSpec(name="full_name", type="text", page=1,
                     x=72, y=120, width=240, height=22, tooltip="Legal name"),
        FB.FieldSpec(name="email", type="text", page=1,
                     x=72, y=160, width=240, height=22, required=True),
        FB.FieldSpec(name="comments", type="multiline", page=1,
                     x=72, y=200, width=300, height=70),
        FB.FieldSpec(name="agree", type="checkbox", page=1,
                     x=72, y=290, width=16, height=16, required=True),
        FB.FieldSpec(name="country", type="dropdown", page=2,
                     x=72, y=120, width=160, height=22,
                     options=["UK", "US", "DE"], default="US"),
        FB.FieldSpec(name="sign_here", type="signature", page=2,
                     x=72, y=200, width=200, height=50),
    ]


# ------------------------------------------------------------- building

def test_builds_a_genuinely_fillable_form():
    output = FB.build(corpus.multipage_pdf(2), specs())
    report = forms.inspect(output)

    assert report.has_form and report.fillable and not report.is_xfa
    assert {f.name for f in report.fields} == {
        "full_name", "email", "comments", "agree", "country", "sign_here",
    }


def test_field_types_survive_the_round_trip():
    report = forms.inspect(FB.build(corpus.multipage_pdf(2), specs()))
    kinds = {f.name: f.kind for f in report.fields}

    assert kinds["full_name"] == "text"
    assert kinds["comments"] == "multiline"
    assert kinds["agree"] == "checkbox"
    assert kinds["country"] == "dropdown"
    assert kinds["sign_here"] == "signature"


def test_required_flags_and_options_survive():
    report = forms.inspect(FB.build(corpus.multipage_pdf(2), specs()))
    assert set(report.required_names) == {"email", "agree"}

    country = next(f for f in report.fields if f.name == "country")
    assert country.options == ["UK", "US", "DE"]


def test_fields_land_on_the_right_page_with_geometry():
    report = forms.inspect(FB.build(corpus.multipage_pdf(2), specs()))
    by_name = {f.name: f for f in report.fields}

    assert by_name["full_name"].page == 1
    assert by_name["country"].page == 2
    assert len(by_name["email"].rect) == 4


def test_view_coordinates_are_converted_to_pdf_space():
    """y is measured from the top in the request, from the bottom in the PDF."""
    output = FB.build(corpus.multipage_pdf(1), [
        FB.FieldSpec(name="probe", type="text", page=1,
                     x=100, y=50, width=200, height=20),
    ])
    rect = next(f for f in forms.inspect(output).fields).rect
    # The page is 792 tall: y=50 from the top is 792-50-20 = 722 from the bottom.
    assert rect[1] == pytest.approx(722, abs=1)
    assert rect[3] == pytest.approx(742, abs=1)


def test_the_built_form_can_actually_be_filled():
    """The whole point: it must work with the existing fill endpoint."""
    output = FB.build(corpus.multipage_pdf(2), specs())
    filled = forms.fill(output, {
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "agree": "/Yes",
    })

    values = {f.name: f.value for f in forms.inspect(filled).fields}
    assert values["full_name"] == "Ada Lovelace"
    assert values["email"] == "ada@example.com"
    assert values["agree"] == "/Yes"


def test_existing_page_content_is_preserved():
    output = FB.build(corpus.multipage_pdf(2), specs())
    text = PdfReader(io.BytesIO(output)).pages[0].extract_text() or ""
    assert "Sample document text." in text


# ------------------------------------------------------------ validation

def test_duplicate_names_are_rejected():
    duplicated = [
        FB.FieldSpec(name="same", type="text", page=1, x=1, y=1, width=10, height=10),
        FB.FieldSpec(name="same", type="text", page=1, x=1, y=40, width=10, height=10),
    ]
    with pytest.raises(PDFEngineError, match="Duplicate field name"):
        FB.build(corpus.clean_pdf(), duplicated)


def test_invalid_name_is_rejected():
    with pytest.raises(PDFEngineError, match="is invalid"):
        FB.build(corpus.clean_pdf(), [
            FB.FieldSpec(name="1 bad name", type="text", page=1,
                         x=1, y=1, width=10, height=10),
        ])


def test_out_of_range_page_is_rejected():
    with pytest.raises(PDFEngineError, match="has 1 page"):
        FB.build(corpus.clean_pdf(), [
            FB.FieldSpec(name="f", type="text", page=9, x=1, y=1, width=10, height=10),
        ])


def test_dropdown_needs_options():
    with pytest.raises(PDFEngineError, match="at least two options"):
        FB.build(corpus.clean_pdf(), [
            FB.FieldSpec(name="pick", type="dropdown", page=1,
                         x=1, y=1, width=50, height=20, options=["only"]),
        ])


def test_unknown_type_is_rejected():
    with pytest.raises(PDFEngineError, match="Unsupported field type"):
        FB.build(corpus.clean_pdf(), [
            FB.FieldSpec(name="f", type="hologram", page=1,
                         x=1, y=1, width=10, height=10),
        ])


def test_verification_rejects_a_form_that_is_not_fillable(monkeypatch):
    """If the build silently produced no fields, it must raise, not return."""
    from docintel.pdf import forms as forms_module

    class NotAForm:
        has_form = False
        fillable = False
        is_xfa = False
        fields: list = []
        note = ""

    monkeypatch.setattr(forms_module, "inspect", lambda data: NotAForm())

    with pytest.raises(PDFEngineError, match="did not come back as a fillable form"):
        FB.build(corpus.clean_pdf(), [
            FB.FieldSpec(name="f", type="text", page=1, x=1, y=1, width=10, height=10),
        ])


def test_adding_fields_to_an_existing_form_keeps_the_old_ones():
    base = corpus.fillable_form_pdf()
    output = FB.build(base, [
        FB.FieldSpec(name="extra_note", type="text", page=1,
                     x=72, y=400, width=200, height=20),
    ])

    names = {f.name for f in forms.inspect(output).fields}
    assert "extra_note" in names
    assert "email" in names          # from the original form


# ------------------------------------------------------------------ api

def test_build_form_endpoint(alice):
    document_id = alice.upload(corpus.multipage_pdf(2)).json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{document_id}/form/builder", json={
        "fields": [
            {"name": "full_name", "type": "text", "page": 1,
             "x": 72, "y": 120, "width": 240, "height": 22},
            {"name": "agree", "type": "checkbox", "page": 1,
             "x": 72, "y": 160, "width": 16, "height": 16, "required": True},
        ],
    })
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert "verified" in response.json()["note"]

    described = alice.get(f"/api/v1/documents/{document_id}/form/builder").json()
    assert described["fillable"] is True
    assert {f["name"] for f in described["fields"]} == {"full_name", "agree"}


def test_build_form_rejects_bad_input_with_a_useful_message(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = alice.post(f"/api/v1/documents/{document_id}/form/builder", json={
        "fields": [{"name": "a b", "type": "text", "page": 1,
                    "x": 1, "y": 1, "width": 10, "height": 10}],
    })
    assert response.status_code == 400
    assert "is invalid" in response.json()["detail"]


def test_other_tenant_cannot_build_a_form(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.post(f"/api/v1/documents/{document_id}/form/builder", json={
        "fields": [{"name": "f", "type": "text", "page": 1,
                    "x": 1, "y": 1, "width": 10, "height": 10}],
    }).status_code == 404


# -------------------------------------------------------- version history

def test_version_history_lists_every_version(alice):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document_id}/pages/rotate",
               json={"pages": [1], "degrees": 90})
    alice.post(f"/api/v1/documents/{document_id}/watermark", json={"text": "DRAFT"})

    body = alice.get(f"/api/v1/documents/{document_id}/versions").json()
    assert body["current"] == 3
    assert [v["version"] for v in body["versions"]] == [3, 2, 1]
    assert body["versions"][-1]["label"] == "original"
    assert "nothing is overwritten" in body["note"]


def test_restore_brings_an_earlier_version_forward(alice):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    original = alice.get(f"/api/v1/documents/{document_id}/download").content

    alice.post(f"/api/v1/documents/{document_id}/pages/delete", json={"pages": [2]})
    assert len(PdfReader(io.BytesIO(
        alice.get(f"/api/v1/documents/{document_id}/download").content)).pages) == 2

    response = alice.post(f"/api/v1/documents/{document_id}/versions/restore",
                          json={"version": 1})
    assert response.status_code == 200
    assert response.json()["restored_from"] == 1

    restored = alice.get(f"/api/v1/documents/{document_id}/download").content
    assert restored == original


def test_restore_is_additive_so_it_can_itself_be_undone(alice):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document_id}/pages/delete", json={"pages": [2]})
    two_pages = alice.get(f"/api/v1/documents/{document_id}/download").content

    alice.post(f"/api/v1/documents/{document_id}/versions/restore", json={"version": 1})
    # Version 2 (the deletion) still exists and can be restored again.
    alice.post(f"/api/v1/documents/{document_id}/versions/restore", json={"version": 2})

    assert alice.get(f"/api/v1/documents/{document_id}/download").content == two_pages

    history = alice.get(f"/api/v1/documents/{document_id}/versions").json()
    assert history["current"] == 4


def test_restore_reuses_stored_bytes_rather_than_duplicating(alice):
    document_id = alice.upload(corpus.multipage_pdf(2)).json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document_id}/watermark", json={"text": "X"})

    body = alice.post(f"/api/v1/documents/{document_id}/versions/restore",
                      json={"version": 1}).json()
    assert body["reused_existing_bytes"] is True


def test_restoring_the_current_version_is_refused(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = alice.post(f"/api/v1/documents/{document_id}/versions/restore",
                          json={"version": 1})
    assert response.status_code == 409


def test_restoring_a_missing_version_is_refused(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert alice.post(f"/api/v1/documents/{document_id}/versions/restore",
                      json={"version": 99}).status_code == 404


def test_viewer_cannot_restore(alice, bob):
    document_id = alice.upload(corpus.multipage_pdf(2)).json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document_id}/watermark", json={"text": "X"})
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "viewer"})

    assert bob.get(f"/api/v1/documents/{document_id}/versions").status_code == 200
    assert bob.post(f"/api/v1/documents/{document_id}/versions/restore",
                    json={"version": 1}).status_code == 403
