import sqlglot
from sqlglot.errors import SqlglotError
from src.core.exceptions import InvalidSQLQueryError
from src.core.logger import logger

class SQLValidator:
    def __init__(self):
        # We only allow these specific AST node types (mostly SELECT)
        self.forbidden_types = (
            sqlglot.exp.Drop,
            sqlglot.exp.Delete,
            sqlglot.exp.Update,
            sqlglot.exp.Alter,
            sqlglot.exp.Create,
            sqlglot.exp.Command,
            sqlglot.exp.Insert,
            # Add others as needed to be absolutely safe
        )

    def validate_query(self, query: str) -> str:
        """
        Parses the SQL using DuckDB dialect and ensures it's safe (SELECT only).
        Returns the parsed query string if valid, raises InvalidSQLQueryError otherwise.
        """
        logger.info(f"Validating SQL query:\n{query}")
        try:
            # Parse using duckdb dialect
            parsed = sqlglot.parse_one(query, dialect="duckdb")
        except SqlglotError as e:
            # Covers both ParseError and TokenError (e.g. an unterminated string literal from
            # an unescaped apostrophe) -- either way the input isn't valid SQL.
            logger.error(f"SQL Parse Error: {str(e)}")
            raise InvalidSQLQueryError(f"Failed to parse SQL: {str(e)}")
            
        # Check if the root node is a Select
        if not isinstance(parsed, sqlglot.exp.Select):
            # Sometime it might be a union or something, but we strictly reject any forbidden types.
            if isinstance(parsed, self.forbidden_types):
                raise InvalidSQLQueryError("Only SELECT queries are allowed. Destructive operations are forbidden.")

        # Traverse the AST to ensure no forbidden operations are nested
        for node in parsed.walk():
            # node is a tuple of (node, parent, key) in walk(), wait, in newer sqlglot walk yields nodes or (node, parent, key)? 
            # In sqlglot 23+, walk() yields generator of nodes. Actually, walk() yields nodes directly.
            # wait, it yields instances of sqlglot.exp.Expression.
            if isinstance(node, self.forbidden_types):
                raise InvalidSQLQueryError("Only SELECT queries are allowed. Destructive operations are forbidden in nested queries.")
                
        # Also reject COPY, ATTACH, DETACH if they somehow pass as Command or aren't caught
        upper_query = query.upper()
        forbidden_keywords = ["COPY ", "ATTACH ", "DETACH "]
        for keyword in forbidden_keywords:
            if keyword in upper_query:
                raise InvalidSQLQueryError(f"Keyword {keyword.strip()} is forbidden.")
                
        return parsed.sql(dialect="duckdb")
