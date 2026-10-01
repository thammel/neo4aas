"""Unit tests for AASX environment extraction (no Neo4j).

The environment root must be recognised in every metamodel namespace: a V3.1
package used to yield nothing and import as empty.
"""

import io
import zipfile

from aas_mapping.aas_neo4j_adapter.aasx import iter_aasx_environments

_ENV = (
    '<environment xmlns="https://admin-shell.io/aas/{ns}">'
    "<submodels><submodel><idShort>Sm</idShort><id>urn:sm/{ns}</id></submodel></submodels>"
    "</environment>"
)


def _aasx(tmp_path, entries: dict[str, str]):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    path = tmp_path / "pkg.aasx"
    path.write_bytes(buf.getvalue())
    return str(path)


def test_reads_30_and_31_environments(tmp_path):
    path = _aasx(tmp_path, {
        "aasx/a/a.aas.xml": _ENV.format(ns="3/0"),
        "aasx/b/b.aas.xml": _ENV.format(ns="3/1"),
    })
    ids = sorted(sm["id"] for env in iter_aasx_environments(path) for sm in env["submodels"])
    assert ids == ["urn:sm/3/0", "urn:sm/3/1"]


def test_skips_non_environment_and_malformed_xml(tmp_path):
    path = _aasx(tmp_path, {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "aasx/broken.xml": "<environment>",
        "aasx/a/a.aas.xml": _ENV.format(ns="3/1"),
    })
    envs = list(iter_aasx_environments(path))
    assert [sm["id"] for env in envs for sm in env["submodels"]] == ["urn:sm/3/1"]
