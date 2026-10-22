class BusinessRuleViolation(Exception):
    default_code = "business_rule_violation"

    def __init__(self, message: str, code: str = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class InsufficientBalanceError(BusinessRuleViolation):
    default_code = "insufficient_balance"


class InvalidStateTransitionError(BusinessRuleViolation):
    default_code = "invalid_state_transition"


class ActiveSessionExistsError(BusinessRuleViolation):
    default_code = "active_session_exists"


class UnauthorizedSessionActionError(BusinessRuleViolation):
    default_code = "unauthorized_session_action"
