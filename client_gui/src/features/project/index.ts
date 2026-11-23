// Public surface of features/project. App consumes only this: the
// store factory, the domain types, and the top-level ProjectDialog component. App mounts
// ProjectDialog once and opens it via `useProjectStore().openDialog()` — it
// never reaches ProjectList/ProjectRow/ProjectCreateDialog or the API/persistence helpers
// directly, so those stay internal to this feature. The group tree types are exported for
// features that read the project tree through the store (Designer, the earlier change).
export { useProjectStore } from './store'
// Group tree types and Flow file context read models (the earlier change
// ) — type-only, so the runtime surface stays the store factory + ProjectDialog.
// adds the Base / review file diff and Git status row types the Designer
// Git part reads through the same store loaders.
// adds the recent Project / Group / Flow entries and the Group / Flow create
// models the Designer File menu uses through the store.
// adds the Flow Canvas read / layout save models the Designer Center reads
// through the store loaders (loadFlowCanvas / saveFlowCanvasLayout), and the handler shape of
// the store's Canvas change subscription (subscribeFlowCanvasChanges).
// S3+S4 adds the Graph definition models (draft save, validation, confirmation)
// the Designer Center saves through the store loaders.
// adds the Graph node run state the Inspector reads (FlowCanvasNodeRun).
// adds the card document lines of a Graph / draft node (FlowCanvasCardDocument).
// adds the Graph run of a Flow (FlowRun / FlowRunNode) the top band runs through the store.
// adds what a Graph Time Machine of a run's done node did or why it was refused.
// adds the Base remote state of git/status (ProjectGitUnpushed*) and the
// group scope floor of the change list (GroupScopeFloor) the Designer Git tab reads.
// adds a Graph node's card title state (FlowCanvasTitleState), saved through the store loader.
// adds the source command models (refusal, download, upload result) the Designer context menu
// runs through the store.
// adds the subscription and frame shapes of the store's project event
// subscription (subscribeProjectEvents, the hub the Canvas subscription also runs on).
// adds the Designer F5 snapshot shape the Designer Shell saves / restores through the store.
export type { RoleRunFields, RoleWaitReason } from './types'
export type { FlowArchiveState } from './flowArchiveApi'
export type { CardRefMatch } from './cardRefApi'
export type { AiInvocation, AiInvocationsPage } from './aiInvocationsApi'
// adds the result of one step / full command (the run result windows read it through the store).
export type {
  FlowCommandFailure,
  FlowCommandMessage,
  FlowCommandModel,
  FlowCommandNode,
  FlowCommandResult,
  FlowCommandRoleUse,
  FlowCommandState,
} from './commandResultApi'
export type { FlowCanvasChangeHandlers } from './flowCanvasEvents'
export type { DesignerSessionSnapshot, DesignerSessionTab } from './designerSessionStorage'
// The Designer Dock sizes the Shell reads / saves through the store.
export type { DesignerDockSizesInput, StoredDesignerDockSizes } from './designerDockSizesApi'
export type { ProjectEventFrame, ProjectEventSubscription } from './projectEvents'
export type {
  BaseFileDiff,
  CreateFlowInput,
  WorkBaseOptions,
  FlowWorkBase,
  FlowWorkBaseUpdate,
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
