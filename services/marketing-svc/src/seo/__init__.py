"""Growth / SEO engine: sites, the task queue the SEO agents work from,
and everything they act on (keywords, research, content, technical
issues, AI-answer visibility, links, conversion, programmatic pages,
local profiles, reports).

The rules that keep autonomous work safe live here, not in the agents:
an agent (any request carrying X-AI-Initiated) is refused at the same
endpoints a person uses whenever a hard gate applies, so no prompt or
tool choice can get around them."""
