-- flowgate.v02.0135 CH0006: per-Agent remote working directory (e.g. /tmp/flowgate/${AGENT_JOB_ID}).
-- NULL = not set; the server substitutes ${AGENT_JOB_ID} and sends the result as the Job cwd.
ALTER TABLE agents ADD COLUMN remote_workdir TEXT NULL;
