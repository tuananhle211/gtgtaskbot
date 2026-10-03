"""Human approval decisions for both workflows.

Script approval and video approval are modelled as two distinct decision
types on purpose (see ADR-002). They share this module only for the
common shape: decision, decider, reason, timestamp, audit link.

TODO(milestone-2): ``ApprovalDecision`` entity + Telegram inline-keyboard flow.
"""
