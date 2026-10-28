// Public surface of features/project (WP0005 T#1 §6/T#2 §8). App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` (D0004 §4.1) — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature. The group tree types are exported for
// features that read the project tree through the store (Designer, flowgate.v02.0023).
export { useProjectStore } from './store'
// Group tree types (flowgate.v02.0023) and Flow file context read models (flowgate.v02.0024
// T0005) — type-only, so the runtime surface stays the store factory + ProjectDialog.
// flowgate.v02.0033 adds the Base / review file diff and Git status row types the Designer
// Git part reads through the same store loaders.
// flowgate.v02.0043 adds the recent Project / Group / Flow entries and the Group / Flow create
// models the Designer File menu uses through the store.
// flowgate.v02.0045 adds the Flow Canvas read / layout save models the Designer Center reads
// through the store loaders (loadFlowCanvas / saveFlowCanvasLayout), and the handler shape of
// the store's Canvas change subscription (subscribeFlowCanvasChanges).
// flowgate.v02.0045 S3+S4 adds the Graph definition models (draft save, validation, confirmation)
// the Designer Center saves through the store loaders.
// flowgate.v02.0048 T#1 adds the Graph node run state the Inspector reads (FlowCanvasNodeRun).
// flowgate.v02.0053 T#5 adds the card document lines of a Graph / draft node (FlowCanvasCardDocument).
// flowgate.v02.0053 T#9 adds the Graph run of a Flow (FlowRun / FlowRunNode) the top band runs through the store.
// flowgate.v02.0061 T#15 adds what a Graph Time Machine of a run's done node did or why it was refused.
// flowgate.v02.0066 T#5 adds the Base remote state of git/status (ProjectGitUnpushed*) and the
// group scope floor of the change list (GroupScopeFloor) the Designer Git tab reads.
// flowgate.v02.0064 T#4 adds a Graph node's card title state (FlowCanvasTitleState), saved through the store loader.
// flowgate.v02.0060 T#2 adds the source command models (refusal, download, upload result) the Designer context menu
// runs through the store.
// flowgate.v02.0048 T#2 adds the subscription and frame shapes of the store's project event
// subscription (subscribeProjectEvents, the hub the Canvas subscription also runs on).
export type { FlowCanvasChangeHandlers } from './flowCanvasEvents'
export type { ProjectEventFrame, ProjectEventSubscription } from './projectEvents'
export type {
  BaseFileDiff,
  CreateFlowInput,
  WorkBaseOptions,
  CreateGroupInput,
  CreateProjectInput,
  CreatedFlow,
  CreatedGroup,
  FetchGroupTreeOptions,
  FlowAiActivity,
  FlowChangeEntry,
  FlowCanvas,
  FlowCanvasCardDocument,
  FlowCanvasDocument,
  FlowCanvasEdge,
  FlowCanvasExternalRelation,
  FlowCanvasGraphBlock,
  FlowCanvasGraphNode,
  FlowCanvasLayoutInput,
  FlowCanvasLayoutResult,
  FlowCanvasNode,
  FlowCanvasNodeRun,
  FlowCanvasOwnership,
  FlowCanvasPosition,
  FlowCanvasSlot,
  FlowCanvasTitleState,
  FlowCanvasView,
  FlowCanvasViewport,
  FlowChangeSet,
  FlowDiffSide,
  FlowFileDiff,
  FlowFileNode,
  FlowFileTree,
  FlowGraphAssignee,
  FlowGraphConfirmResult,
  FlowGraphDefinition,
  FlowGraphDefinitionEdge,
  FlowGraphDefinitionNode,
  FlowGraphDraftEdgeInput,
  FlowGraphDraftInput,
  FlowGraphDraftNodeInput,
  FlowGraphDraftSaveResult,
  FlowGraphIssue,
  FlowGraphValidation,
  FlowRef,
  FlowRun,
  FlowRunApproval,
  FlowRunNode,
  FlowRunTimeMachine,
  FlowRunTimeMachineReason,
  FlowRunTimeMachineRefusal,
  GroupScopeFloor,
  GroupTreeNode,
  GroupTreeNodeKind,
  MergeReviewFileDiff,
  Project,
  ProjectGitBaseDirty,
  ProjectGitBaseUntracked,
  ProjectGitPendingPlan,
  ProjectGitPendingRow,
  ProjectGitSlot,
  ProjectGitStatus,
  ProjectGitUnpushed,
  ProjectGitUnpushedMerge,
  ProjectModule,
  RecentFlow,
  RecentGroup,
  RecentProject,
  SourceDownload,
  SourceMutationRefusal,
  SourceUploadResult,
} from './types'
export { default as ProjectDialog } from './ProjectDialog.vue'
