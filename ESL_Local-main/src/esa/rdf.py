"""
rdf.py — T-Box parsing and A-Box RDF generation using PyOxigraph.

Architecture alignment:
  - T-Box (ontology) pipeline: parse CENtree Turtle → serialize to N-Quads (→ S3 / local_bucket)
  - A-Box pipeline: read structured records (customers, accounts, loans) → build RDF instances
                    → serialize to N-Quads (→ S3 / local_bucket → Neptune)
"""

import logging
from pathlib import Path
from pyoxigraph import Store, RdfFormat, NamedNode, Literal, Quad, DefaultGraph

log = logging.getLogger(__name__)

EX = "https://example.org/esa/"
RDF_TYPE = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
XSD_STRING  = NamedNode("http://www.w3.org/2001/XMLSchema#string")
XSD_INTEGER = NamedNode("http://www.w3.org/2001/XMLSchema#integer")
XSD_DECIMAL = NamedNode("http://www.w3.org/2001/XMLSchema#decimal")


# ── T-Box ─────────────────────────────────────────────────────────────────────

def parse_tbox(ttl_path: str) -> tuple[bytes, int]:
    """
    Parse the CENtree ontology Turtle file (T-Box vocabulary) and serialize
    it as N-Quads for downstream storage and SHACL validation.

    Input : Turtle file at ttl_path
    Output: (N-Quads bytes, triple count)
    """
    path = Path(ttl_path)
    log.info("[T-Box] Input: Turtle ontology file → %s", path)
    if not path.is_file():
        raise FileNotFoundError(f"Ontology file not found: {path}")

    store = Store()
    store.load(path=str(path), format=RdfFormat.TURTLE)
    triple_count = sum(1 for _ in store)
    nquads = store.dump(format=RdfFormat.N_QUADS)

    log.info("[T-Box] Output: %d RDF triples serialised to N-Quads (in-memory, ready to write)", triple_count)
    return nquads, triple_count


# ── A-Box helpers ─────────────────────────────────────────────────────────────

def _add(store: Store, s: NamedNode, p_local: str, o, dtype=None):
    """Add a quad to the store. o can be a NamedNode or a Python scalar."""
    if isinstance(o, NamedNode):
        store.add(Quad(s, NamedNode(EX + p_local), o, DefaultGraph()))
    else:
        store.add(Quad(s, NamedNode(EX + p_local), Literal(str(o), datatype=dtype or XSD_STRING), DefaultGraph()))


def _type(store: Store, node: NamedNode, class_local: str):
    store.add(Quad(node, RDF_TYPE, NamedNode(EX + class_local), DefaultGraph()))


# ── A-Box ─────────────────────────────────────────────────────────────────────

def build_abox(customers: list[dict], accounts: list[dict], loans: list[dict]) -> tuple[bytes, int]:
    """
    Build an A-Box RDF graph from three structured source tables:
      - customers  → Customer nodes with attributes + registeredAt Branch
      - accounts   → Account nodes with attributes + Customer ownsAccount
      - loans      → Loan nodes with attributes + Customer hasLoan + Loan securedBy Account

    Input : customers list, accounts list, loans list (dicts from JSON files)
    Output: (N-Quads bytes, total triple count)
    """
    log.info("[A-Box] Input: %d customers, %d accounts, %d loans", len(customers), len(accounts), len(loans))
    store = Store()

    # ── Customers ─────────────────────────────────────────────────────────────
    log.info("[A-Box] Building Customer nodes ...")
    customer_count = 0
    branch_ids_seen: set[str] = set()
    for row in customers:
        cid   = str(row.get("customer_id", "")).strip()
        name  = str(row.get("full_name",   "")).strip()
        if not cid or not name:
            log.warning("[A-Box]   Skipping customer row — missing customer_id or full_name: %s", row)
            continue

        customer = NamedNode(EX + "customer/" + cid)
        _type(store, customer, "Customer")
        _add(store, customer, "customerId",  cid)
        _add(store, customer, "fullName",    name)
        _add(store, customer, "email",       row.get("email",  ""))
        _add(store, customer, "phone",       row.get("phone",  ""))
        if row.get("credit_score") is not None:
            _add(store, customer, "creditScore", int(row["credit_score"]), dtype=XSD_INTEGER)

        # Branch relationship
        branch_id = str(row.get("branch_id", "")).strip()
        if branch_id:
            branch = NamedNode(EX + "branch/" + branch_id)
            store.add(Quad(customer, NamedNode(EX + "registeredAt"), branch, DefaultGraph()))
            if branch_id not in branch_ids_seen:
                _type(store, branch, "Branch")
                _add(store, branch, "branchId", branch_id)
                branch_ids_seen.add(branch_id)
                log.info("[A-Box]   Created Branch node: %s", branch_id)

        customer_count += 1
        log.debug("[A-Box]   Customer %s (%s) → Branch %s", cid, name, branch_id)

    log.info("[A-Box] Customers processed: %d | Branches created: %d", customer_count, len(branch_ids_seen))

    # ── Accounts ──────────────────────────────────────────────────────────────
    log.info("[A-Box] Building Account nodes ...")
    account_count = 0
    for row in accounts:
        acc_id  = str(row.get("account_id",  "")).strip()
        cust_id = str(row.get("customer_id", "")).strip()
        if not acc_id or not cust_id:
            log.warning("[A-Box]   Skipping account row — missing account_id or customer_id: %s", row)
            continue

        account  = NamedNode(EX + "account/"  + acc_id)
        customer = NamedNode(EX + "customer/" + cust_id)
        _type(store, account, "Account")
        _add(store, account, "accountId",     acc_id)
        _add(store, account, "accountType",   row.get("account_type", ""))
        _add(store, account, "accountStatus", row.get("status", ""))
        if row.get("balance") is not None:
            _add(store, account, "balance", float(row["balance"]), dtype=XSD_DECIMAL)

        # Customer → ownsAccount → Account
        store.add(Quad(customer, NamedNode(EX + "ownsAccount"), account, DefaultGraph()))

        account_count += 1
        log.debug("[A-Box]   Account %s (%s) linked to Customer %s", acc_id, row.get("account_type"), cust_id)

    log.info("[A-Box] Accounts processed: %d", account_count)

    # ── Loans ─────────────────────────────────────────────────────────────────
    log.info("[A-Box] Building Loan nodes ...")
    loan_count = 0
    for row in loans:
        loan_id  = str(row.get("loan_id",     "")).strip()
        cust_id  = str(row.get("customer_id", "")).strip()
        acc_id   = str(row.get("account_id",  "")).strip()
        if not loan_id or not cust_id:
            log.warning("[A-Box]   Skipping loan row — missing loan_id or customer_id: %s", row)
            continue

        loan     = NamedNode(EX + "loan/"     + loan_id)
        customer = NamedNode(EX + "customer/" + cust_id)
        _type(store, loan, "Loan")
        _add(store, loan, "loanId",       loan_id)
        _add(store, loan, "loanType",     row.get("loan_type", ""))
        _add(store, loan, "loanStatus",   row.get("status", ""))
        if row.get("amount") is not None:
            _add(store, loan, "loanAmount", float(row["amount"]), dtype=XSD_DECIMAL)
        if row.get("interest_rate") is not None:
            _add(store, loan, "interestRate", float(row["interest_rate"]), dtype=XSD_DECIMAL)

        # Customer → hasLoan → Loan
        store.add(Quad(customer, NamedNode(EX + "hasLoan"), loan, DefaultGraph()))

        # Loan → securedBy → Account (if account linked)
        if acc_id:
            account = NamedNode(EX + "account/" + acc_id)
            store.add(Quad(loan, NamedNode(EX + "securedBy"), account, DefaultGraph()))

        loan_count += 1
        log.debug("[A-Box]   Loan %s (%s, %.0f INR) linked to Customer %s", loan_id, row.get("loan_type"), row.get("amount", 0), cust_id)

    log.info("[A-Box] Loans processed: %d", loan_count)

    total = sum(1 for _ in store)
    nquads = store.dump(format=RdfFormat.N_QUADS)
    log.info("[A-Box] Output: Total %d RDF triples built and serialised to N-Quads", total)
    return nquads, total


# ── SHACL (optional) ──────────────────────────────────────────────────────────

def validate_shacl(data_nquads: bytes, shapes_ttl: str) -> dict:
    """
    Run pySHACL validation of A-Box N-Quads against the SHACL shapes graph.

    Input : A-Box N-Quads bytes, path to shapes Turtle file
    Output: dict with 'conforms' (bool) and 'report' (text)
    """
    log.info("[SHACL] Input: A-Box N-Quads in-memory + shapes file → %s", shapes_ttl)
    from pyshacl import validate
    data = data_nquads.decode("utf-8")
    conforms, report_graph, report_text = validate(
        data_graph=data, shacl_graph=shapes_ttl,
        data_graph_format="nquads", shacl_graph_format="turtle", inference="none")
    result = {"conforms": bool(conforms), "report": str(report_text)}
    log.info("[SHACL] Output: conforms=%s", result["conforms"])
    if not result["conforms"]:
        log.warning("[SHACL] Validation FAILED. Review report for details.")
    return result
