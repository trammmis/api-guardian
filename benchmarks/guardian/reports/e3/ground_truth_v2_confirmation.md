# E3-B Ground Truth v2 Confirmation

## Material Passport

- Origin: E3-B pre-system human spot-check
- Confirmation state: `human_confirmed`
- Confirmation timestamp: `2026-09-07T11:20:14.853704+00:00`
- System output used: `none`

## Freeze Evidence

- Preserved v1 aggregate SHA256: `3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- Final v2 aggregate SHA256: `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`
- Final v2 manifest file SHA256: `917c21c4c5eb642c00e6d3399c40b73dc8b9432c328eae7c85f1d7039dd6bf54`
- Production Core hashes: verified against v1 preflight
- E2/E2.5 artifact hashes: verified against v1 preflight
- Guardian/oasdiff System Output before v2 freeze: absent

## Approved Amendment

`rw_netbird_004_agent_network_settings_rework` removes
`POST /api/agent-network/settings` from both ContractAffectedScope and
RuntimeExecutableScope. Contract verdict remains `fail`.
