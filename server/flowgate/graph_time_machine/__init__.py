"""0061 T#12: read-only impact and safety checks for Graph Time Machine.

No restore/reopen route or Git/DB rewind is implemented here. T#13 calls
service.require_safe inside its Flow guard before persisting an operation.
"""
