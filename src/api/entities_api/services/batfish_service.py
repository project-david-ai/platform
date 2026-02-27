# src/api/entities_api/services/batfish_service.py
"""
BatfishService — Tenant-Isolated Snapshot Pipeline
====================================================

Isolation pattern mirrors VectorStoreDBService:
  - Every snapshot is owned by a user_id
  - snapshot_key = f"{user_id}_{snapshot_name}" is the globally unique
    isolation key used for on-disk paths and Batfish network names
  - _require_snapshot_access() enforces ownership on every operation
  - No caller can touch another tenant's snapshot

Pipeline:
  refresh_snapshot()  → ingest configs + push to Batfish + upsert DB record
  run_tool()          → ownership check → dispatch single RCA tool
  run_all_tools()     → ownership check → all tools concurrently
"""

import asyncio
import ipaddress
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Optional

import pandas as pd
from projectdavid_common.utilities.logging_service import LoggingUtility
from pybatfish.client.session import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from src.api.entities_api.models.models import BatfishSnapshot, StatusEnum

logging_utility = LoggingUtility()

GNS3_ROOT = Path(os.getenv("GNS3_ROOT", "/data/gns3"))
SNAPSHOT_ROOT = Path(os.getenv("SNAPSHOT_ROOT", "/data/snapshots"))
BATFISH_HOST = os.getenv("BATFISH_HOST", "batfish")
BATFISH_PORT = int(os.getenv("BATFISH_PORT", 9996))
BATFISH_NETWORK = os.getenv("BATFISH_NETWORK", "gns3_network")

TOOLS = [
    "get_device_os_inventory",
    "get_logical_topology_with_mtu",
    "get_ospf_failures",
    "get_bgp_failures",
    "get_undefined_references",
    "get_unused_structures",
    "get_acl_shadowing",
    "get_routing_loop_detection",
]


# ─────────────────────────────────────────────────────────────────────────────
# CUSTOM EXCEPTIONS  (mirrors VectorStoreDBError pattern)
# ─────────────────────────────────────────────────────────────────────────────


class BatfishServiceError(Exception):
    pass


class BatfishSnapshotNotFoundError(BatfishServiceError):
    pass


class BatfishSnapshotConflictError(BatfishServiceError):
    pass


class BatfishToolError(BatfishServiceError):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE
# ─────────────────────────────────────────────────────────────────────────────


class BatfishService:

    def __init__(self, db: DBSession):
        self.db = db

    # ── Isolation key ─────────────────────────────────────────────────────────

    @staticmethod
    def _make_key(user_id: str, snapshot_name: str) -> str:
        """Globally unique namespaced key — mirrors collection_name in VectorStore."""
        return f"{user_id}_{snapshot_name}"

    # ── Ownership enforcement ─────────────────────────────────────────────────

    def _require_snapshot_access(
        self, snapshot_name: str, user_id: str
    ) -> BatfishSnapshot:
        """
        Mirrors _require_store_access() in the vector store router.
        Raises BatfishSnapshotNotFoundError if not found or not owned by user_id.
        """
        record = (
            self.db.query(BatfishSnapshot)
            .filter(
                BatfishSnapshot.snapshot_name == snapshot_name,
                BatfishSnapshot.user_id == user_id,
                BatfishSnapshot.status != StatusEnum.deleted,
            )
            .first()
        )
        if not record:
            raise BatfishSnapshotNotFoundError(
                f"Snapshot '{snapshot_name}' not found for this user."
            )
        return record

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _upsert_snapshot_record(
        self,
        user_id: str,
        snapshot_name: str,
        snapshot_key: str,
        configs_root: str,
        devices: List[str],
        status: StatusEnum,
        error_message: Optional[str] = None,
    ) -> BatfishSnapshot:
        """
        Insert or update the BatfishSnapshot DB record.
        Mirrors create_vector_store() / update pattern in VectorStoreDBService.
        """
        record = (
            self.db.query(BatfishSnapshot)
            .filter(BatfishSnapshot.snapshot_key == snapshot_key)
            .first()
        )

        now = int(time.time())

        if record:
            record.configs_root = configs_root
            record.device_count = len(devices)
            record.devices = devices
            record.status = status
            record.error_message = error_message
            record.updated_at = now
            if status == StatusEnum.active:
                record.last_ingested_at = now
        else:
            record = BatfishSnapshot(
                id=uuid.uuid4().hex,
                snapshot_name=snapshot_name,
                snapshot_key=snapshot_key,
                user_id=user_id,
                configs_root=configs_root,
                device_count=len(devices),
                devices=devices,
                status=status,
                error_message=error_message,
                created_at=now,
                updated_at=now,
                last_ingested_at=now if status == StatusEnum.active else None,
            )
            self.db.add(record)

        try:
            self.db.commit()
            self.db.refresh(record)
            return record
        except IntegrityError as e:
            self.db.rollback()
            raise BatfishSnapshotConflictError(
                f"Snapshot key conflict for '{snapshot_key}': {e}"
            ) from e
        except Exception as e:
            self.db.rollback()
            raise BatfishServiceError(f"DB error upserting snapshot record: {e}") from e

    def get_snapshot(self, snapshot_name: str, user_id: str) -> BatfishSnapshot:
        return self._require_snapshot_access(snapshot_name, user_id)

    def list_snapshots(self, user_id: str) -> List[BatfishSnapshot]:
        return (
            self.db.query(BatfishSnapshot)
            .filter(
                BatfishSnapshot.user_id == user_id,
                BatfishSnapshot.status != StatusEnum.deleted,
            )
            .order_by(BatfishSnapshot.updated_at.desc())
            .all()
        )

    def delete_snapshot(self, snapshot_name: str, user_id: str) -> bool:
        record = self._require_snapshot_access(snapshot_name, user_id)
        record.status = StatusEnum.deleted
        record.updated_at = int(time.time())
        try:
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            raise BatfishServiceError(f"DB error soft-deleting snapshot: {e}") from e

    # ── Pipeline — ingest ─────────────────────────────────────────────────────

    def _stage_configs(self, snapshot_key: str, configs_root: str) -> List[str]:
        """
        Recursively collect configs from configs_root, extract hostnames,
        and stage into /data/snapshots/{snapshot_key}/configs/.
        Cleans stale configs on each refresh.
        """
        root = Path(configs_root)
        if not root.exists():
            raise FileNotFoundError(f"configs_root not found: {root}")

        candidates = list(root.rglob("*startup-config.cfg"))
        if not candidates:
            candidates = list(root.rglob("*.cfg"))
        if not candidates:
            raise FileNotFoundError(f"No config files found under {root}")

        dest = SNAPSHOT_ROOT / snapshot_key / "configs"
        dest.mkdir(parents=True, exist_ok=True)

        for old in dest.glob("*.cfg"):
            old.unlink()

        staged = []
        for cfg_path in candidates:
            text = cfg_path.read_text(errors="ignore")
            hostname = self._extract_hostname(text) or f"device_{uuid.uuid4().hex[:6]}"
            dest_file = dest / f"{hostname}.cfg"
            if dest_file.exists():
                dest_file = dest / f"{hostname}_{uuid.uuid4().hex[:4]}.cfg"
            shutil.copy(cfg_path, dest_file)
            staged.append(hostname)

        logging_utility.info(
            f"Staged {len(staged)} configs → {dest} (snapshot_key={snapshot_key})"
        )
        return staged

    # ── Pipeline — load snapshot ──────────────────────────────────────────────

    def _push_to_batfish(self, snapshot_key: str):
        """Push staged snapshot directory into Batfish."""
        snapshot_path = SNAPSHOT_ROOT / snapshot_key
        if not snapshot_path.exists():
            raise FileNotFoundError(
                f"Snapshot path not found: {snapshot_path}. Run ingest first."
            )
        bf = Session(host=BATFISH_HOST, port=BATFISH_PORT)
        bf.set_network(BATFISH_NETWORK)
        bf.init_snapshot(str(snapshot_path), name=snapshot_key, overwrite=True)
        logging_utility.info(f"Snapshot '{snapshot_key}' loaded into Batfish")

    # ── Pipeline — full refresh ───────────────────────────────────────────────

    def refresh_snapshot(
        self,
        user_id: str,
        snapshot_name: str,
        configs_root: Optional[str] = None,
    ) -> BatfishSnapshot:
        """
        Full pipeline:
          1. Derive namespaced snapshot_key from user_id + snapshot_name
          2. Stage configs recursively
          3. Push to Batfish
          4. Upsert DB record with ownership + status

        This is the only entry point for creating or refreshing a snapshot.
        """
        snapshot_key = self._make_key(user_id, snapshot_name)
        root = str(configs_root or GNS3_ROOT)

        # Mark as loading
        self._upsert_snapshot_record(
            user_id, snapshot_name, snapshot_key, root, [], StatusEnum.processing
        )

        try:
            devices = self._stage_configs(snapshot_key, root)
        except Exception as e:
            self._upsert_snapshot_record(
                user_id,
                snapshot_name,
                snapshot_key,
                root,
                [],
                StatusEnum.failed,
                error_message=str(e),
            )
            raise BatfishServiceError(f"Config staging failed: {e}") from e

        try:
            self._push_to_batfish(snapshot_key)
        except Exception as e:
            self._upsert_snapshot_record(
                user_id,
                snapshot_name,
                snapshot_key,
                root,
                devices,
                StatusEnum.failed,
                error_message=str(e),
            )
            raise BatfishServiceError(f"Batfish load failed: {e}") from e

        # Mark ready
        return self._upsert_snapshot_record(
            user_id, snapshot_name, snapshot_key, root, devices, StatusEnum.active
        )

    # ── Tool dispatch ─────────────────────────────────────────────────────────

    def _get_bf_session(self, snapshot_key: str) -> Session:
        """Get a Batfish session attached to the tenant's snapshot."""
        bf = Session(host=BATFISH_HOST, port=BATFISH_PORT)
        bf.set_network(BATFISH_NETWORK)
        try:
            bf.set_snapshot(snapshot_key)
        except Exception:
            raise BatfishSnapshotNotFoundError(
                f"Snapshot '{snapshot_key}' not loaded in Batfish. "
                "Call refresh_snapshot first."
            )
        return bf

    async def run_tool(self, user_id: str, snapshot_name: str, tool_name: str) -> str:
        """
        Ownership check → resolve snapshot_key → dispatch tool.
        Called per LLM function call.
        """
        if tool_name not in TOOLS:
            raise BatfishToolError(f"Unknown tool '{tool_name}'. Available: {TOOLS}")

        record = self._require_snapshot_access(snapshot_name, user_id)
        snapshot_key = record.snapshot_key
        bf = self._get_bf_session(snapshot_key)

        method = getattr(self, f"_{tool_name}")
        return await method(bf)

    async def run_all_tools(self, user_id: str, snapshot_name: str) -> dict:
        """All tools concurrently. Ownership checked once, shared across all."""
        record = self._require_snapshot_access(snapshot_name, user_id)
        snapshot_key = record.snapshot_key
        bf = self._get_bf_session(snapshot_key)

        results = await asyncio.gather(
            *[getattr(self, f"_{t}")(bf) for t in TOOLS],
            return_exceptions=True,
        )
        return {
            tool: (str(r) if isinstance(r, Exception) else r)
            for tool, r in zip(TOOLS, results)
        }

    # ── RCA Tool implementations ──────────────────────────────────────────────

    async def _get_device_os_inventory(self, bf: Session) -> str:
        def _fetch():
            return (
                bf.q.nodeProperties(properties="Configuration_Format").answer().frame()
            )

        node_props = await asyncio.to_thread(_fetch)
        if node_props.empty or "Configuration_Format" not in node_props.columns:
            return "=== DEVICE OS INVENTORY ===\nNo OS information could be extracted."
        output = ["=== DEVICE OS / VENDOR INVENTORY ==="]
        for os_format, group in node_props.groupby("Configuration_Format"):
            nodes = group["Node"].astype(str).tolist()
            output.append(f"\nOS Platform: [{os_format}]")
            output.append(f"  └─ Devices ({len(nodes)}): {', '.join(nodes)}")
        return "\n".join(output)

    async def _get_logical_topology_with_mtu(self, bf: Session) -> str:
        def _fetch():
            iface_props = (
                bf.q.interfaceProperties(properties="Primary_Address,Active,MTU")
                .answer()
                .frame()
            )
            ospf_props = bf.q.ospfInterfaceConfiguration().answer().frame()
            return iface_props, ospf_props

        iface_props, ospf_props = await asyncio.to_thread(_fetch)
        iface_props["Node"] = iface_props["Interface"].apply(lambda x: x.hostname)
        iface_props["Int"] = iface_props["Interface"].apply(lambda x: x.interface)
        if not ospf_props.empty:
            ospf_props["Node"] = ospf_props["Interface"].apply(lambda x: x.hostname)
            ospf_props["Int"] = ospf_props["Interface"].apply(lambda x: x.interface)
            merged_df = pd.merge(
                iface_props,
                ospf_props[["Node", "Int", "OSPF_Area_Name"]],
                on=["Node", "Int"],
                how="left",
            )
        else:
            merged_df = iface_props
            merged_df["OSPF_Area_Name"] = "None"
        l3_ifaces = merged_df.dropna(subset=["Primary_Address"]).copy()

        def get_subnet(ip_val):
            try:
                return str(ipaddress.IPv4Interface(str(ip_val)).network)
            except Exception:
                return "Unknown"

        l3_ifaces["Subnet"] = l3_ifaces["Primary_Address"].apply(get_subnet)
        l3_ifaces["OSPF_Area_Name"] = l3_ifaces["OSPF_Area_Name"].fillna("None")
        l3_ifaces["Summary"] = l3_ifaces.apply(
            lambda row: f"  └─ {row['Node']}:{row['Int']} "
            f"(IP: {row['Primary_Address']}, Area: {row['OSPF_Area_Name']}, "
            f"Active: {row['Active']}, MTU: {row['MTU']})",
            axis=1,
        )
        mtu_mismatch_flags = l3_ifaces.groupby("Subnet")["MTU"].nunique() > 1
        logical_subnets = (
            l3_ifaces.groupby("Subnet")["Summary"].apply(list).reset_index()
        )
        output = ["=== LOGICAL L3 TOPOLOGY (GROUPED BY SUBNET WITH MTU) ==="]
        for _, row in logical_subnets.iterrows():
            subnet = row["Subnet"]
            warn = "[⚠️ MTU MISMATCH DETECTED]" if mtu_mismatch_flags[subnet] else ""
            output.append(f"\nNetwork: {subnet}{warn}")
            output.extend(row["Summary"])
        return "\n".join(output)

    async def _get_ospf_failures(self, bf: Session) -> str:
        def _fetch():
            return bf.q.ospfSessionCompatibility().answer().frame()

        sessions = await asyncio.to_thread(_fetch)
        if sessions.empty or "Session_Status" not in sessions.columns:
            return "=== OSPF FAILURES ===\nAll configured OSPF sessions are healthy or none exist."
        failed = sessions[sessions["Session_Status"] != "ESTABLISHED"]
        if failed.empty:
            return (
                "=== OSPF FAILURES ===\nAll configured OSPF sessions are established."
            )
        return f"=== OSPF FAILURES ===\n{failed.to_string()}"

    async def _get_bgp_failures(self, bf: Session) -> str:
        def _fetch():
            return bf.q.bgpSessionCompatibility().answer().frame()

        sessions = await asyncio.to_thread(_fetch)
        if sessions.empty or "Configured_Status" not in sessions.columns:
            return "=== BGP FAILURES ===\nNo BGP sessions exist or status cannot be verified."
        failed = sessions[sessions["Configured_Status"] != "UNIQUE_MATCH"]
        if failed.empty:
            return "=== BGP FAILURES ===\nAll configured BGP sessions are compatible and healthy."
        output = ["=== BGP SESSION FAILURES DETECTED ==="]
        for node, group in failed.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Peer IP: {row.get('Remote_IP','?')} | "
                    f"Local AS: {row.get('Local_AS','?')} -> Remote AS: {row.get('Remote_AS','?')} | "
                    f"Status: [⚠️ {row.get('Configured_Status','?')}]"
                )
        return "\n".join(output)

    async def _get_undefined_references(self, bf: Session) -> str:
        def _fetch():
            return bf.q.undefinedReferences().answer().frame()

        refs = await asyncio.to_thread(_fetch)
        if refs.empty:
            return "=== CONFIGURATION HYGIENE ===\nNo undefined references found."
        output = ["=== UNDEFINED REFERENCES (POSSIBLE TYPOS/MISSING CONFIG) ==="]
        for node, group in refs.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Missing[{row.get('Structure_Type','?')}]: '{row.get('Structure_Name','?')}' "
                    f"| Called by: {row.get('Context','?')} | File Lines: {row.get('Source_Lines','?')}"
                )
        return "\n".join(output)

    async def _get_unused_structures(self, bf: Session) -> str:
        def _fetch():
            return bf.q.unusedStructures().answer().frame()

        unused = await asyncio.to_thread(_fetch)
        if unused.empty:
            return "=== DEAD CODE ANALYSIS ===\nNo unused structures found."
        output = ["=== UNUSED STRUCTURES (DEAD CODE / ORPHANED CONFIG) ==="]
        for node, group in unused.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ Unused [{row.get('Structure_Type','?')}]: '{row.get('Structure_Name','?')}'"
                )
        return "\n".join(output)

    async def _get_acl_shadowing(self, bf: Session) -> str:
        def _fetch():
            return bf.q.filterLineReachability().answer().frame()

        acl_lines = await asyncio.to_thread(_fetch)
        if acl_lines.empty:
            return (
                "=== ACL SHADOWING ===\nNo shadowed or unreachable ACL lines detected."
            )
        shadowed = acl_lines[acl_lines["Unreachable_Line"].notna()]
        if shadowed.empty:
            return "=== ACL SHADOWING ===\nAll ACL lines are reachable (no shadowing detected)."
        output = ["=== ACL SHADOWING (UNREACHABLE FILTER LINES) ==="]
        for node, group in shadowed.groupby("Node"):
            output.append(f"\nNode: {node}")
            for _, row in group.iterrows():
                output.append(
                    f"  └─ ACL [{row.get('Filter_Name','?')}] | "
                    f"Shadowed Line: '{row.get('Unreachable_Line','?')}' | "
                    f"Action: {row.get('Unreachable_Line_Action','?')} | "
                    f"Blocked By: {row.get('Blocking_Lines','?')}"
                )
        return "\n".join(output)

    async def _get_routing_loop_detection(self, bf: Session) -> str:
        def _fetch():
            return bf.q.detectLoops().answer().frame()

        loops = await asyncio.to_thread(_fetch)
        if loops.empty:
            return "=== ROUTING LOOP DETECTION ===\nNo forwarding loops detected across the data plane."
        output = ["=== ROUTING LOOPS DETECTED (DATA-PLANE SIMULATION) ==="]
        for node, group in loops.groupby("Node"):
            output.append(f"\nLoop Origin Node: {node}")
            for _, row in group.iterrows():
                loop_path = row.get("Loop", "Unknown")
                path_str = (
                    " → ".join(str(h) for h in loop_path)
                    if isinstance(loop_path, list)
                    else str(loop_path)
                )
                output.append(
                    f"  └─ Ingress Interface: {row.get('Ingress_Interface','?')}\n"
                    f"     Loop Path: {path_str} [⚠️ LOOPS BACK TO ORIGIN]"
                )
        return "\n".join(output)

    # ── Health ────────────────────────────────────────────────────────────────

    def check_health(self) -> dict:
        import urllib.request

        url = f"http://{BATFISH_HOST}:{BATFISH_PORT}/v2/question_templates"
        try:
            urllib.request.urlopen(url, timeout=5)
            return {"status": "reachable", "host": BATFISH_HOST, "port": BATFISH_PORT}
        except Exception as e:
            raise ConnectionError(f"Batfish unreachable: {e}")

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_hostname(cfg_text: str) -> str:
        match = re.search(r"^hostname\s+(\S+)", cfg_text, re.MULTILINE)
        return match.group(1) if match else ""
