/**
 * flowgate.v02.0059 T#2 (0016-TR rework) — 자동 배치 from the server's own Canvas responses to the server's own
 * coordinates, on the real read path: the project store's Canvas loader (fetchFlowCanvas with
 * `collapse=conversation`, as every Designer read asks) -> toFlowCanvas -> the server Graph source -> the Center
 * session -> autoLayout. Only `@shared/api` is mocked: it answers SERVER_CANVAS for the query it gets, and the
 * v0.1 document read with the Flow's documents.
 *
 * SERVER_CANVAS is what the server returned for these Flows — server legacy_projector.project and
 * canvas_service._place / _collapse run unchanged in Python (no saved layout), once with
 * `collapse=conversation` and once without. With no saved layout the server's read shows its auto placement,
 * so after the shown cards are moved away, 자동 배치 (scale 1) must bring each back where the server put it —
 * a lone v0.1 root or a lone slot on the chain at (0, 0), the documents the read left out keeping their place
 * in the order, a root that replaced older root documents found through their supersede chain.
 */
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { documentRead } from '@/features/document'
import { useProjectStore } from '@/features/project'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'

const getRequest = vi.hoisted(() => vi.fn())
const putRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest: vi.fn(),
  putRequest,
  patchRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

interface ServerFlow {
  collapsed: Record<string, any>
  whole: Record<string, any>
  documents: Array<Record<string, unknown>>
}

// Server output (see the file comment): p.ui.0011 a v0.1 root with no slot yet, a conversation between its
// documents; p.ui.0012 a v0.1 Flow whose root was cancelled, one slot left; p.ui.0013 an unassigned Flow with
// documents R, CH, D; p.ui.0014 a v0.1 Flow with no slot whose root was replaced twice (0001-R -> 0002-R ->
// 0005-R); p.ui.0015 a v0.1 Flow with no slot whose root was cancelled and whose first document replaced an
// older one; p.ui.0016 a v0.1 root with two slots and conversations between the documents.
const SERVER_CANVAS: Record<string, ServerFlow> = {
  "p.ui.0011": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0011",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0011.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0011.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0011.0002-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0002-NR", "type_code": "NR", "seq": 2, "title": "NR 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0011.0004-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0004-D", "type_code": "D", "seq": 4, "title": "D 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 780, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {"conversation": 1},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0011",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0011.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0011.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0011.0002-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0002-NR", "type_code": "NR", "seq": 2, "title": "NR 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0011.0003-CH", "identity_source": "document", "origin": "document", "kind": "memo", "doc_type": "CH", "title": "CH 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0003-CH", "type_code": "CH", "seq": 3, "title": "CH 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0011.0004-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0011.0004-D", "type_code": "D", "seq": 4, "title": "D 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 780, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0011.0001-R", "group_id": "p.ui.0011", "status": "open", "superseded_by": null, "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0011.0002-NR", "group_id": "p.ui.0011", "status": "open", "superseded_by": null, "type_code": "NR", "seq": 2, "title": "NR 2"},
      {"doc_id": "p.ui.0011.0003-CH", "group_id": "p.ui.0011", "status": "open", "superseded_by": null, "type_code": "CH", "seq": 3, "title": "CH 3"},
      {"doc_id": "p.ui.0011.0004-D", "group_id": "p.ui.0011", "status": "open", "superseded_by": null, "type_code": "D", "seq": 4, "title": "D 4"},
    ],
  },
  "p.ui.0012": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0012",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0012.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "slot_21", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "task", "doc_type": "T", "title": "T#1", "state": "draft", "editable": {"move": true, "structure": false}, "document": null, "slot": {"item_id": 21, "item_seq": 1, "order": 1, "type": "T", "label": "T#1", "derived_status": "pending", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0012.0002-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0012.0002-D", "type_code": "D", "seq": 2, "title": "D 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0012",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0012.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "slot_21", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "task", "doc_type": "T", "title": "T#1", "state": "draft", "editable": {"move": true, "structure": false}, "document": null, "slot": {"item_id": 21, "item_seq": 1, "order": 1, "type": "T", "label": "T#1", "derived_status": "pending", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0012.0002-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0012.0002-D", "type_code": "D", "seq": 2, "title": "D 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0012.0001-R", "group_id": "p.ui.0012", "status": "cancelled", "superseded_by": null, "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0012.0002-D", "group_id": "p.ui.0012", "status": "open", "superseded_by": null, "type_code": "D", "seq": 2, "title": "D 2"},
    ],
  },
  "p.ui.0013": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0013",
      "project_id": "p",
      "ownership": {"state": "unassigned", "sequence_root_doc_id": null, "graph_revision_no": null, "assigned_at": null},
      "projection": "documents",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0013.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0013.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0013.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0013.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 0, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {"conversation": 1},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0013",
      "project_id": "p",
      "ownership": {"state": "unassigned", "sequence_root_doc_id": null, "graph_revision_no": null, "assigned_at": null},
      "projection": "documents",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0013.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0013.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0013.0002-CH", "identity_source": "document", "origin": "document", "kind": "memo", "doc_type": "CH", "title": "CH 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0013.0002-CH", "type_code": "CH", "seq": 2, "title": "CH 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0013.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0013.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 0, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0013.0001-R", "group_id": "p.ui.0013", "status": "open", "superseded_by": null, "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0013.0002-CH", "group_id": "p.ui.0013", "status": "open", "superseded_by": null, "type_code": "CH", "seq": 2, "title": "CH 2"},
      {"doc_id": "p.ui.0013.0003-D", "group_id": "p.ui.0013", "status": "open", "superseded_by": null, "type_code": "D", "seq": 3, "title": "D 3"},
    ],
  },
  "p.ui.0014": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0014",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0014.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0014.0005-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 5", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0005-R", "type_code": "R", "seq": 5, "title": "R 5", "doc_review_status": "approved", "status": "open", "supersedes": ["p.ui.0014.0002-R"]}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0014.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0014.0004-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0004-D", "type_code": "D", "seq": 4, "title": "D 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0014",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0014.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0014.0005-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 5", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0005-R", "type_code": "R", "seq": 5, "title": "R 5", "doc_review_status": "approved", "status": "open", "supersedes": ["p.ui.0014.0002-R"]}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0014.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0014.0004-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0014.0004-D", "type_code": "D", "seq": 4, "title": "D 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0014.0001-R", "group_id": "p.ui.0014", "status": "open", "superseded_by": "p.ui.0014.0002-R", "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0014.0002-R", "group_id": "p.ui.0014", "status": "open", "superseded_by": "p.ui.0014.0005-R", "type_code": "R", "seq": 2, "title": "R 2"},
      {"doc_id": "p.ui.0014.0003-D", "group_id": "p.ui.0014", "status": "open", "superseded_by": null, "type_code": "D", "seq": 3, "title": "D 3"},
      {"doc_id": "p.ui.0014.0004-D", "group_id": "p.ui.0014", "status": "open", "superseded_by": null, "type_code": "D", "seq": 4, "title": "D 4"},
      {"doc_id": "p.ui.0014.0005-R", "group_id": "p.ui.0014", "status": "open", "superseded_by": null, "type_code": "R", "seq": 5, "title": "R 5"},
    ],
  },
  "p.ui.0015": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0015",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0015.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0015.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0015.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": ["p.ui.0015.0002-D"]}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0015.0004-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0015.0004-NR", "type_code": "NR", "seq": 4, "title": "NR 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0015",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0015.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0015.0003-D", "identity_source": "document", "origin": "document", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0015.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": ["p.ui.0015.0002-D"]}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0015.0004-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0015.0004-NR", "type_code": "NR", "seq": 4, "title": "NR 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
      ],
      "edges": [],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0015.0001-R", "group_id": "p.ui.0015", "status": "cancelled", "superseded_by": null, "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0015.0002-D", "group_id": "p.ui.0015", "status": "open", "superseded_by": "p.ui.0015.0003-D", "type_code": "D", "seq": 2, "title": "D 2"},
      {"doc_id": "p.ui.0015.0003-D", "group_id": "p.ui.0015", "status": "open", "superseded_by": null, "type_code": "D", "seq": 3, "title": "D 3"},
      {"doc_id": "p.ui.0015.0004-NR", "group_id": "p.ui.0015", "status": "open", "superseded_by": null, "type_code": "NR", "seq": 4, "title": "NR 4"},
    ],
  },
  "p.ui.0016": {
    "collapsed": {
      "ok": true,
      "flow_id": "p.ui.0016",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0016.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0016.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "slot_61", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": {"item_id": 61, "item_seq": 1, "order": 1, "type": "D", "label": "D#1", "derived_status": "in_progress", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 0, "source": "auto"}},
        {"node_key": "slot_62", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "task", "doc_type": "T", "title": "T#2", "state": "draft", "editable": {"move": true, "structure": false}, "document": null, "slot": {"item_id": 62, "item_seq": 2, "order": 2, "type": "T", "label": "T#2", "derived_status": "pending", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0005-TR", "identity_source": "document", "origin": "document", "kind": "task", "doc_type": "TR", "title": "TR 5", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0005-TR", "type_code": "TR", "seq": 5, "title": "TR 5", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 780, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0006-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 6", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0006-NR", "type_code": "NR", "seq": 6, "title": "NR 6", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 1040, "y": 180, "source": "auto"}},
      ],
      "edges": [
        {"edge_id": "seq:doc:p.ui.0016.0001-R>slot_61", "edge_class": "execution", "origin": "sequence", "source": "doc:p.ui.0016.0001-R", "target": "slot_61", "relation": null, "condition": null, "editable": false},
        {"edge_id": "seq:slot_61>slot_62", "edge_class": "execution", "origin": "sequence", "source": "slot_61", "target": "slot_62", "relation": null, "condition": null, "editable": false},
      ],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": ["conversation"],
        "ignored_keys": [],
        "hidden_counts": {"conversation": 2},
      },
    },
    "whole": {
      "ok": true,
      "flow_id": "p.ui.0016",
      "project_id": "p",
      "ownership": {"state": "v01", "sequence_root_doc_id": "p.ui.0016.0001-R", "graph_revision_no": null, "assigned_at": null},
      "projection": "sequence",
      "view": null,
      "graph": {"draft": null, "latest_revision_no": null, "root_binding": null},
      "nodes": [
        {"node_key": "doc:p.ui.0016.0001-R", "identity_source": "document", "origin": "document", "kind": "requirement", "doc_type": "R", "title": "R 1", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0001-R", "type_code": "R", "seq": 1, "title": "R 1", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 0, "y": 0, "source": "auto"}},
        {"node_key": "slot_61", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "design", "doc_type": "D", "title": "D 3", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0003-D", "type_code": "D", "seq": 3, "title": "D 3", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": {"item_id": 61, "item_seq": 1, "order": 1, "type": "D", "label": "D#1", "derived_status": "in_progress", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 0, "source": "auto"}},
        {"node_key": "slot_62", "identity_source": "sequence_item", "origin": "sequence_slot", "kind": "task", "doc_type": "T", "title": "T#2", "state": "draft", "editable": {"move": true, "structure": false}, "document": null, "slot": {"item_id": 62, "item_seq": 2, "order": 2, "type": "T", "label": "T#2", "derived_status": "pending", "protected": false, "source_wp_card_id": null}, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 0, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0002-Q", "identity_source": "document", "origin": "document", "kind": "memo", "doc_type": "Q", "title": "Q 2", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0002-Q", "type_code": "Q", "seq": 2, "title": "Q 2", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 260, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0004-CH", "identity_source": "document", "origin": "document", "kind": "memo", "doc_type": "CH", "title": "CH 4", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0004-CH", "type_code": "CH", "seq": 4, "title": "CH 4", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 520, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0005-TR", "identity_source": "document", "origin": "document", "kind": "task", "doc_type": "TR", "title": "TR 5", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0005-TR", "type_code": "TR", "seq": 5, "title": "TR 5", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 780, "y": 180, "source": "auto"}},
        {"node_key": "doc:p.ui.0016.0006-NR", "identity_source": "document", "origin": "document", "kind": "research", "doc_type": "NR", "title": "NR 6", "state": "done", "editable": {"move": true, "structure": false}, "document": {"doc_id": "p.ui.0016.0006-NR", "type_code": "NR", "seq": 6, "title": "NR 6", "doc_review_status": "approved", "status": "open", "supersedes": []}, "slot": null, "graph_node": null, "node_run": null, "position": {"x": 1040, "y": 180, "source": "auto"}},
      ],
      "edges": [
        {"edge_id": "seq:doc:p.ui.0016.0001-R>slot_61", "edge_class": "execution", "origin": "sequence", "source": "doc:p.ui.0016.0001-R", "target": "slot_61", "relation": null, "condition": null, "editable": false},
        {"edge_id": "seq:slot_61>slot_62", "edge_class": "execution", "origin": "sequence", "source": "slot_61", "target": "slot_62", "relation": null, "condition": null, "editable": false},
      ],
      "external_relations": [],
      "viewport": null,
      "run": null,
      "identity": {"sequence_tag": null, "rematched": false, "retired_node_keys": []},
      "collapsed": {
        "applied": [],
        "ignored_keys": [],
        "hidden_counts": {},
      },
    },
    "documents": [
      {"doc_id": "p.ui.0016.0001-R", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "R", "seq": 1, "title": "R 1"},
      {"doc_id": "p.ui.0016.0002-Q", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "Q", "seq": 2, "title": "Q 2"},
      {"doc_id": "p.ui.0016.0003-D", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "D", "seq": 3, "title": "D 3"},
      {"doc_id": "p.ui.0016.0004-CH", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "CH", "seq": 4, "title": "CH 4"},
      {"doc_id": "p.ui.0016.0005-TR", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "TR", "seq": 5, "title": "TR 5"},
      {"doc_id": "p.ui.0016.0006-NR", "group_id": "p.ui.0016", "status": "open", "superseded_by": null, "type_code": "NR", "seq": 6, "title": "NR 6"},
    ],
  },
}

const reads: Array<{ url: string; collapse: unknown }> = []
let failWholeRead = false

beforeEach(() => {
  reads.length = 0
  failWholeRead = false
  getRequest.mockReset()
  putRequest.mockReset()
  putRequest.mockImplementation(async () => ({ data: { ok: true, saved_positions: 1, viewport_saved: false, ignored: [] } }))
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    reads.push({ url, collapse: params.collapse })
    const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
    if (canvas) {
      const flow = SERVER_CANVAS[decodeURIComponent(canvas[1])]
      if (params.collapse === 'conversation') return { data: flow.collapsed }
      if (params.collapse === undefined && !failWholeRead) return { data: flow.whole }
      throw new Error(`no canvas for collapse=${String(params.collapse)}`)
    }
    const document = /^\/api\/v1\/documents\/([^/]+)$/.exec(url)
    if (document) {
      const id = decodeURIComponent(document[1])
      const found = Object.values(SERVER_CANVAS).flatMap((flow) => flow.documents).find((doc) => doc.doc_id === id)
      if (found) return { data: found }
    }
    throw new Error(`unexpected request ${url}`)
  })
})

async function openSession(flowId: string) {
  setActivePinia(createPinia())
  const store = useProjectStore()
  // The project store's own Canvas loader / saver (the Shell passes the whole store and documentRead).
  const source = createServerFlowGraphSource(
    { loadFlowCanvas: store.loadFlowCanvas, saveFlowCanvasLayout: store.saveFlowCanvasLayout },
    documentRead,
  )
  const center = createDesignerCenterSession({ source, layoutSaveDelayMs: 10 })
  center.setProject('p')
  center.openFlow({ projectId: 'p', flowId, flowName: flowId })
  await flushPromises()
  const shown = () => {
    const load = center.state.graphs[flowId]
    if (load?.status !== 'ready') throw new Error('not ready')
    return load.graph as FlowGraph
  }
  const positions = () => Object.fromEntries(shown().nodes.map((node) => [node.id, [node.x, node.y]]))
  /** Every shown card somewhere else (one undo entry), so 자동 배치 has to place each again. */
  const scatter = () =>
    center.moveNodes(flowId, shown().nodes.map((node, i) => ({ nodeId: node.id, x: 2000 + 300 * i, y: 1500 + 40 * i })))
  return { center, shown, positions, scatter }
}

/** The server's position of every card the collapsed read shows (its read without collapse, same layout). */
function serverPositions(flowId: string) {
  const flow = SERVER_CANVAS[flowId]
  const shown = new Set(flow.collapsed.nodes.map((node: any) => node.node_key))
  return Object.fromEntries(
    flow.whole.nodes.filter((node: any) => shown.has(node.node_key)).map((node: any) => [node.node_key, [node.position.x, node.position.y]]),
  )
}

const wholeReads = () => reads.filter((read) => /\/canvas$/.test(read.url) && read.collapse === undefined).length
const documentReads = () => reads.filter((read) => /\/documents\//.test(read.url)).map((read) => read.url.split('/').pop())

describe('자동 배치 on the real Canvas read path gives the server coordinates (flowgate.v02.0059 T#2)', () => {
  it('the fixture is the server read: collapse hid the conversations only after placing every card', () => {
    for (const [flowId, flow] of Object.entries(SERVER_CANVAS)) {
      const whole = Object.fromEntries(flow.whole.nodes.map((node: any) => [node.node_key, node.position]))
      for (const node of flow.collapsed.nodes) expect(node.position, flowId).toEqual(whole[node.node_key])
    }
    expect(SERVER_CANVAS['p.ui.0013'].collapsed.collapsed.hidden_counts).toEqual({ conversation: 1 })
  })

  // flowgate.v02.0068 T#2 (0004-D §4): the explicit 자동정렬 is no longer the server's own placement — the read and
  // the server's input (the whole Flow, the supersede chain) stay the same; the last column is where 가로 자동정렬
  // puts the shown cards (0005-L §2.6: the chain first, the documents in one row below it, a hidden document keeping
  // its slot and its real place as an obstacle).
  it.each([
    ['p.ui.0011', 'a v0.1 root with no slot is the chain: (0, 0), its documents below, D past the hidden conversation', 1, [],
      { 'doc:p.ui.0011.0001-R': [0, 0], 'doc:p.ui.0011.0002-NR': [0, 200], 'doc:p.ui.0011.0004-D': [780, 200] }],
    ['p.ui.0012', 'a lone slot (root cancelled) is the chain: (0, 0)', 0, [],
      { slot_21: [0, 0], 'doc:p.ui.0012.0002-D': [0, 200] }],
    ['p.ui.0013', 'documents R, CH, D with CH hidden: R (0, 0), D (520, 0)', 1, [],
      { 'doc:p.ui.0013.0001-R': [0, 0], 'doc:p.ui.0013.0003-D': [520, 0] }],
    ['p.ui.0014', 'a root replaced twice: found through the supersede chain, (0, 0)', 1,
      ['p.ui.0014.0001-R', 'p.ui.0014.0002-R', 'p.ui.0014.0005-R'],
      { 'doc:p.ui.0014.0005-R': [0, 0], 'doc:p.ui.0014.0003-D': [0, 200], 'doc:p.ui.0014.0004-D': [260, 200] }],
    ['p.ui.0015', 'a cancelled root: the first document that replaced another stays a document', 1, ['p.ui.0015.0001-R'],
      { 'doc:p.ui.0015.0003-D': [0, 0], 'doc:p.ui.0015.0004-NR': [260, 0] }],
    ['p.ui.0016', 'root + two slots, two conversations hidden between the documents', 1, [],
      { 'doc:p.ui.0016.0001-R': [0, 0], slot_61: [260, 0], slot_62: [520, 0], 'doc:p.ui.0016.0005-TR': [780, 200], 'doc:p.ui.0016.0006-NR': [1040, 200] }],
  ] as const)('%s: %s', async (flowId, _title, whole, documents, laidOut) => {
    const { center, positions, scatter } = await openSession(flowId)
    const server = serverPositions(flowId)
    expect(positions()).toEqual(server)
    expect(reads.filter((read) => /\/canvas$/.test(read.url)).map((read) => read.collapse)).toEqual(['conversation'])
    expect(scatter()).toBe(true)
    expect(positions()).not.toEqual(server)
    expect(await center.autoLayout(flowId, 'horizontal')).toBe(true)
    expect(positions()).toEqual(laidOut)
    expect(wholeReads()).toBe(whole)
    expect(documentReads()).toEqual(documents)
    // One 실행 취소 takes the 자동정렬 back.
    expect(center.undoEdit(flowId)).toBe(true)
    expect(positions()).not.toEqual(laidOut)
  })

  it('the reviewer case in numbers: R (0, 0) and D (520, 0) beside the hidden CH, horizontal; R (0, 0) and D (0, 352) vertical', async () => {
    const { center, positions, scatter } = await openSession('p.ui.0013')
    scatter()
    await center.autoLayout('p.ui.0013', 'horizontal')
    expect(positions()).toEqual({ 'doc:p.ui.0013.0001-R': [0, 0], 'doc:p.ui.0013.0003-D': [520, 0] })
    await center.autoLayout('p.ui.0013', 'vertical')
    expect(positions()).toEqual({ 'doc:p.ui.0013.0001-R': [0, 0], 'doc:p.ui.0013.0003-D': [0, 352] })
  })

  it('a shown Graph that leaves nothing out is laid out at once, with no further read', async () => {
    const { center, positions, scatter } = await openSession('p.ui.0012')
    scatter()
    expect(center.autoLayout('p.ui.0012', 'horizontal')).toBe(true)
    expect(positions()).toEqual({ slot_21: [0, 0], 'doc:p.ui.0012.0002-D': [0, 200] })
    expect(wholeReads()).toBe(0)
  })

  it('only the latest 자동 배치 of a Flow moves the cards; a failed whole read moves nothing', async () => {
    const { center, positions, scatter } = await openSession('p.ui.0013')
    scatter()
    const before = positions()
    const wide = center.autoLayout('p.ui.0013', 'vertical')
    const normal = center.autoLayout('p.ui.0013', 'horizontal')
    expect(await wide).toBe(false)
    expect(await normal).toBe(true)
    expect(positions()).toEqual({ 'doc:p.ui.0013.0001-R': [0, 0], 'doc:p.ui.0013.0003-D': [520, 0] })
    center.undoEdit('p.ui.0013')
    expect(positions()).toEqual(before)
    failWholeRead = true
    expect(await center.autoLayout('p.ui.0013', 'horizontal')).toBe(false)
    expect(positions()).toEqual(before)
  })
})
