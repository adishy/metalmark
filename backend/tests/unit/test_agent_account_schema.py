"""An agent's account subtype is a code the mirror shows as it is (ADR-0062).

If the write schema accepted a subtype the read policy does not recognise, the
value would come back as a pseudonym: free text by another name.
"""

import typing

from app.agent import policies
from app.schemas.agent import AgentAccountCreate, AgentAccountSubtype


def test_every_subtype_an_agent_may_send_is_a_known_code():
    assert set(typing.get_args(AgentAccountSubtype)) <= policies.ACCOUNT_SUBTYPES.allowed


def test_an_account_is_named_by_the_agent_or_marked_as_its_own():
    base = {"type": "depository", "currency": "USD"}
    assert AgentAccountCreate(**base).stored_name() == "Added by agent"
    assert AgentAccountCreate(**base, name="  Roth \n IRA ").stored_name() == "Roth IRA"
