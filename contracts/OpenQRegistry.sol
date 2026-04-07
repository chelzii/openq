pragma solidity >=0.6.10 <0.8.20;

contract OpenQRegistry {
    struct IdentityRecord {
        bool exists;
        string publicKey;
        string label;
    }

    struct AuditRecord {
        bool exists;
        string requestId;
        string digest;
        string status;
        string blockedLayer;
        string app;
        string action;
        uint256 blockNumber;
        uint256 recordedAt;
    }

    address public owner;

    mapping(string => IdentityRecord) private identities;
    mapping(string => mapping(string => bool)) private permissions;
    mapping(string => AuditRecord) private audits;
    string[] private auditIndex;

    modifier onlyOwner() {
        require(msg.sender == owner, "only owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function registerIdentity(string memory did, string memory publicKey, string memory label) public onlyOwner {
        identities[did] = IdentityRecord({exists: true, publicKey: publicKey, label: label});
    }

    function getIdentity(string memory did) public view returns (bool, string memory, string memory) {
        IdentityRecord storage record = identities[did];
        return (record.exists, record.publicKey, record.label);
    }

    function setPermission(string memory did, string memory permissionKey, bool allowed) public onlyOwner {
        require(identities[did].exists, "identity_not_found");
        permissions[did][permissionKey] = allowed;
    }

    function getPermission(string memory did, string memory permissionKey) public view returns (bool, bool) {
        return (identities[did].exists, permissions[did][permissionKey]);
    }

    function recordAudit(
        string memory requestId,
        string memory digest,
        string memory status,
        string memory blockedLayer,
        string memory app,
        string memory action
    ) public onlyOwner {
        AuditRecord storage existing = audits[requestId];
        if (!existing.exists) {
            auditIndex.push(requestId);
        }
        audits[requestId] = AuditRecord({
            exists: true,
            requestId: requestId,
            digest: digest,
            status: status,
            blockedLayer: blockedLayer,
            app: app,
            action: action,
            blockNumber: block.number,
            recordedAt: block.timestamp
        });
    }

    function getAudit(string memory requestId)
        public
        view
        returns (
            bool,
            string memory,
            string memory,
            string memory,
            string memory,
            string memory,
            uint256,
            uint256
        )
    {
        AuditRecord storage record = audits[requestId];
        return (
            record.exists,
            record.digest,
            record.status,
            record.blockedLayer,
            record.app,
            record.action,
            record.blockNumber,
            record.recordedAt
        );
    }

    function getAuditCount() public view returns (uint256) {
        return auditIndex.length;
    }

    function getAuditByIndex(uint256 index)
        public
        view
        returns (
            string memory,
            string memory,
            string memory,
            string memory,
            string memory,
            string memory,
            uint256,
            uint256
        )
    {
        require(index < auditIndex.length, "audit_index_out_of_range");
        AuditRecord storage record = audits[auditIndex[index]];
        return (
            record.requestId,
            record.digest,
            record.status,
            record.blockedLayer,
            record.app,
            record.action,
            record.blockNumber,
            record.recordedAt
        );
    }
}
