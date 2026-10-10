-- 009: a session is deleted 2 hours after its last use (the Zero Data promise), not 22.5 hours after it began.
--
-- last_used is set when the session is made and every time the rider's phone reads it (the plan, the 20 s
-- "ways back" poll, a re-run). The API deletes any session idle for 2 hours on every save and never returns one,
-- so a search erases itself 2 hours after the rider stops using it. The 004 retention policy (chunks dropped
-- after 22.5 h) stays as a backstop for a session someone keeps open all day.

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_used timestamptz NOT NULL DEFAULT now();
