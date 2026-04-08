# OPA policy used by pattern 3 (external authz, agent-side).
#
# The AGENT asks OPA "is this user allowed to invoke this tool?" before making
# the tool call. Coarse role-based check; the agent has the JWT and includes
# the rich user claims in the input.
#
# Input shape:
#   {
#     "user": {"role": "...", "department": "...", "reports_to": "..."},
#     "tool": "get_expenses" | "search_documents" | "approve_expense",
#     "action": "read" | "approve"
#   }

package agentauth.agent_side

import rego.v1

# Default deny.
default allow := false

# Admins can call any tool.
allow if {
	input.user.role == "admin"
}

# Managers can call read tools and approve_expense (their job).
allow if {
	input.user.role == "manager"
	input.tool in {"get_expenses", "search_documents", "approve_expense"}
}

# Employees can call read tools only -- they cannot approve their own expenses.
allow if {
	input.user.role == "employee"
	input.tool in {"get_expenses", "search_documents"}
}

# Structured decision the agent can log.
decision := {
	"allow": true,
	"reason": "permitted",
} if {
	allow
}

decision := {
	"allow": false,
	"reason": "role not permitted to invoke this tool",
} if {
	not allow
}
