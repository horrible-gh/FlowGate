// flowgate.v02.0048 T#3 — public surface of the document API feature (D#1 §2.3 / §6.2). The
// Designer imports `@/features/document` only through this file. It holds `export *` lines and
// nothing else, so a feature set fills its own api file without editing it (D#1 §6.3); export
// names start with their file's area (`qa…`, `attachments…`) so two files never clash.
export * from './documentError'
export * from './api/document'
export * from './api/review'
export * from './api/qa'
export * from './api/attachments'
export * from './api/settings'
export * from './api/kindSwitch'
export * from './api/testSpec'
export * from './api/check'
export * from './api/workPlan'
export * from './api/run'
export * from './api/memo'
