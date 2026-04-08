# OPA policy used by pattern 7 (external authz, tool-side).
#
# The TOOL (FastAPI service) asks OPA "is this user allowed to act on a
# resource owned by this target user?" after validating the inbound JWT.
# Fine-grained, ReBAC-style check that consults the relationships loaded
# from data.json.
#
# Input shape:
#   {
#     "user_id":         "<username of caller, e.g. 'bob'>",
#     "target_user_id":  "<username whose resource is being accessed, e.g. 'alice'>",
#     "action":          "read" | "approve",
#     "resource_type":   "expense" | "document"
#   }

package agentauth.tool_side

import rego.v1

# Default deny.
default allow := false

# Admins can do anything.
allow if {
	input.user_id in data.admins
}

# Users can always read their own resources.
allow if {
	input.action == "read"
	input.user_id == input.target_user_id
}

# Managers can read AND approve resources owned by their direct reports.
# data.relationships.manages is a map of {manager_username: [report1, report2, ...]}.
# Use object.get to default to [] when the caller has no managed reports.
allow if {
	input.action in {"read", "approve"}
	reports := object.get(data.relationships.manages, input.user_id, [])
	input.target_user_id in reports
}

# Members of the same department can read each other's resources (read only).
# data.relationships.members is {department_name: [user1, user2, ...]}.
allow if {
	input.action == "read"
	some department
	input.user_id in data.relationships.members[department]
	input.target_user_id in data.relationships.members[department]
}

# Structured decision the tool can log.
decision := {
	"allow": true,
	"reason": reason_for_allow,
} if {
	allow
}

decision := {
	"allow": false,
	"reason": "no rule permits this action on this target",
} if {
	not allow
}

reason_for_allow := "admin override" if {
	input.user_id in data.admins
}

reason_for_allow := "self access" if {
	input.user_id == input.target_user_id
	not input.user_id in data.admins
}

reason_for_allow := "manager-of-target relationship" if {
	reports := object.get(data.relationships.manages, input.user_id, [])
	input.target_user_id in reports
	input.user_id != input.target_user_id
	not input.user_id in data.admins
}

reason_for_allow := "same-department peer read" if {
	input.action == "read"
	some department
	input.user_id in data.relationships.members[department]
	input.target_user_id in data.relationships.members[department]
	input.user_id != input.target_user_id
	reports := object.get(data.relationships.manages, input.user_id, [])
	not input.target_user_id in reports
	not input.user_id in data.admins
}
