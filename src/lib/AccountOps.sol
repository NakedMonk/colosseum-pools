// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {CoreOps} from "./CoreOps.sol";

/// @title AccountOps
/// @notice What a ruled account (Pool, ChallengeAccount) does on HyperCore that takes the most code:
///         closing its positions at a crossing price, choosing the keyless address of a stop, and
///         approving an agent by name for a term. A linked library: its public functions run by
///         DELEGATECALL in the account's own context, so CoreWriter still sees the account as the
///         sender. Out of the accounts, which HIP-3 brought over the size a contract may have (the
///         CTO, 21 October 2026).
library AccountOps {
    /// @notice A reduce-only IOC order for every open position among `perps`; how many were open.
    function closePositions(address account, uint32[] memory perps, uint16 slippageBps) public returns (uint256) {
        return CoreOps.closePositions(account, perps, slippageBps);
    }

    function keylessAddress(address account, uint256 nonce, bytes32 salt) public view returns (address) {
        return CoreOps.keylessAddress(account, nonce, salt);
    }

    function setAgent(address agent, uint256 validUntilMs) public {
        CoreOps.setAgent(agent, validUntilMs);
    }
}
