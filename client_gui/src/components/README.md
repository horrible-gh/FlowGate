# components

Only shared, domain-independent UI belongs here (T0005 §4). UI used by a single
feature or screen belongs to that features/* or app/* owner.

## ui/

T0004 introduced the first App Shell primitives: `Button`, `IconButton`, `Input`,
`Textarea`, `Select`, `Toggle`, `Tabs`, `Badge`, `Tooltip`, and `Dropdown`. They
own visual/interaction contracts only, with no direct API, store, router, or
domain-state dependencies. Features that understand domain state own mappings
to colors/labels, such as Git conflict or approval status.

T0006 introduced overlay shells: `ModalShell` (blocking, Teleport, backdrop, ESC,
focus trap/return), `ModelessShell` (nonblocking, drag/resize/minimize, no backdrop
or focus trap), and `ConfirmDialog`/`AlertDialog` built on ModalShell. These are
domain-independent foundations, not Git/AI/Project/Workflow dialogs. Callers own
business copy through title/message/label props. `../core/overlay/` shares only
the z-index allocation and focus restoration actually duplicated across shells;
the focus trap stays inside ModalShell, its only user.

T0008 added status/feedback primitives: `Toast` (temporary nonblocking feedback,
emitting `dismiss` after `duration` or closing manually), `ToastHost` (a shared
stacking region; the caller owns its message queue/global store), `Alert`
(persistent inline/banner warning or information, nonblocking unlike AlertDialog),
`EmptyState` (healthy but no data), `LoadingState` (awaiting results), and
`ErrorState` (screen/panel errors). They know no copy, trigger conditions, or retry
logic: features supply title/message/slots. They do not access APIs/stores/routers.
Badge's existing info/success/warning/danger tones moved to shared
`--fg2-status-*` tokens. Badge and Toast reuse foreground/border/background;
Alert reuses borders and ErrorState foreground (`../app/styles/theme.css`,
T0008 §11). Badge's neutral dashed/transparent style remains outside this token set.

T0010 added the final shared primitives: `SplitPane` (a boundary moved by dragging
or arrow keys, `#first`/`#second` slots, `direction="horizontal|vertical"`),
`ResizablePane` (one independent panel edge, with no knowledge of its neighbor),
and `ContextMenu` (nonblocking pointer-position menu opened by right-click or
keyboard, unlike Dropdown's trigger-element positioning). They do not know
whether their contents are Designer Explorer or Git history, and do not persist
size/position to localStorage or APIs. SplitPane and ResizablePane shared actual
drag/keyboard-step/min-max-clamp/listener-lifecycle code, extracted to
`../core/interaction/axisResize.ts`; their APIs stay separate per T0010 §7.
Handle thickness and hover/active colors use shared `--fg2-splitter-*` tokens
in `../app/styles/theme.css`.

ContextMenu reuses `allocateOverlayZIndex()` from `core/overlay/zIndex.ts`.
This revealed that ModalShell calculated surface z-index as `overlayZIndex + 1`
without consuming the second shared allocation. ModalShell was minimally changed
to allocate again for its surface, preventing a menu opened inside it from
receiving the same z-index. Its document `focusin` trap likewise permits focus
under an element with a higher z-index, allocated after the modal opened, such
as a ContextMenu inside it.
