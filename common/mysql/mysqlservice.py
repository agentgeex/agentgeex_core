try:
    from .mysqlconn import UsingMysql  # Package-import case

    __all__ = [
        "executeSql",
        "UsingMysql",
        "saveRow",
        "updateRowByPk",
        "updateRow",
        "updateBySql",
        "getRowByPk",
        "getRowByPkOne",
        "getRowCount4Sql",
        "getRowsByCondition",
        "getRowsBySQl",
        "getRowsByFullSQl",
        "getRowsBySQlNoCondtion",
        "getRowsBySQlCase",
        "getSQL2KeyValueMap",
        "getSQL2Map4Value",
        "getSQL2Map",
        "getSQL2Arr",
        "getSQL2Map4Arr",
        "getSQL2Map4bean",
        "getSQL2Map4keyArr",
        "getSQL2Map2Map",
        "getSQL2Map2Map4ayy",
        "getSQL2KeyValueArr",
    ]
except ImportError:
    from mysqlconn import UsingMysql  # Direct-run case


# Generic parameterized SQL execution (single entry for reads and writes)
def executeSql(sql, params=None, commit=False):
    """Execute parameterized SQL. Returns lastrowid when commit=True, otherwise fetchall."""
    try:
        with UsingMysql(commit=commit, log_time=False) as um:
            um.cursor.execute(sql, params or ())
            return um.cursor.lastrowid if commit else um.cursor.fetchall()
    except Exception:
        import traceback

        print(f"executeSql FAILED: {sql[:200]}", file=__import__("sys").stderr)
        traceback.print_exc()
        raise


# Insert one record
def saveRow(tablename, columnValues):

    addSqlParams = []
    updatesqlcolumn = []
    updatesqlflag = []

    for key, value in columnValues.items():
        addSqlParams.append(key)
        updatesqlcolumn.append(value)
        updatesqlflag.append("%s")

    addSqlParamsstr = ",".join(addSqlParams)
    updatesqlflagstr = ",".join(updatesqlflag)

    addSql = f"INSERT INTO {tablename}( {addSqlParamsstr} ) VALUES( {updatesqlflagstr}  )"

    # #logger.debug(addSql)

    with UsingMysql(log_time=True) as um:
        um.cursor.execute(addSql, updatesqlcolumn)
        return um.cursor.lastrowid


# Update one record by primary key
def updateRowByPk(tablename, columnAndValue, pkName, pkValue):

    updateSqlParams = []
    updateParms = []

    _updateparm = " set 1=1 "

    for key, value in columnAndValue.items():
        updateSqlParams.append(value)
        updateParms.append(f"{key}=%s")

    _updatewhereparm = " (1=1)  "
    _searchparm = {pkName: pkValue}

    # for key, value in _searchparm.items():
    #     updateSqlParams.append(value)
    #     _updatewhereparm = _updatewhereparm + f" and {key} = %s "

    updateSqlParams.append(pkValue)

    updateParmsStr = ",".join(updateParms)
    addSql = f"UPDATE {tablename} set {updateParmsStr} WHERE {pkName} = %s"

    # #logger.debug(addSql)

    with UsingMysql(log_time=True) as um:
        um.cursor.execute(addSql, updateSqlParams)


## By query condition
def updateRow(tablename, columnAndValue, condition):

    updateSqlParams = []
    updateParms = []

    _updateparm = " set 1=1 "

    for key, value in columnAndValue.items():
        updateSqlParams.append(value)
        updateParms.append(f"{key}=%s")

    _updatewhereparm = " (1=1)  "
    _searchparm = condition

    for key, value in _searchparm.items():
        updateSqlParams.append(value)
        _updatewhereparm = _updatewhereparm + f" and {key} = %s "

    updateParmsStr = ",".join(updateParms)
    addSql = f"UPDATE {tablename} set {updateParmsStr} WHERE  {_updatewhereparm}"

    # #logger.debug(addSql)

    with UsingMysql(log_time=True) as um:
        um.cursor.execute(addSql, updateSqlParams)


# Update records by SQL statement
def updateBySql(updateSql):
    # #logger.debug(updateSql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(updateSql)
        # Return the affected row count so callers can do CAS (compare-and-swap) claim checks.
        # * 2026-10-26: this used to return None, which made the watchdog's "claim lock" useless
        #   (UPDATE ... SET needs_reply=2 looked successful for everyone; no way to tell whether the claim won).
        #   All 14 existing callers ignore the return value, so adding one is backward compatible.
        return um.cursor.rowcount


## Fetch one record by primary key
def getRowByPk(tablename, column="*", pkColumn="", value=""):

    sql = f"select {column} from {tablename} where {pkColumn} = %s"
    # #logger.debug(sql)
    params = value

    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql, params)
        result_data = um.cursor.fetchone()
        return result_data


##Fetch one record by primary key
def getRowByPkOne(sql):

    # #logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchone()
        return result_data


# Count rows by SQL statement
def getRowCount4Sql(sql, numscolumn):

    # #logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchone()
        return result_data[numscolumn]


# Fetch a record set from a specific table by query condition
def getRowsByCondition(tablename, column, condition, orderBy, limit):

    if column is None or column == "":
        column = "*"

    search_where_parm = " (1=1)  "
    search_value_parm = []
    _addSqlParams = []

    for key, value in condition.items():
        search_value_parm.append(value)
        search_where_parm = search_where_parm + f" and {key}=%s"

    search_sql = f" select {column} from {tablename}  where {search_where_parm} {orderBy}  {limit}  "

    # #logger.debug(search_sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(search_sql, search_value_parm)
        result_data = um.cursor.fetchall()
        return result_data


# Fetch a data set by SQL statement
def getRowsBySQl(sqlchareter, condition, orderBy, limit):

    search_where_parm = " (1=1)  "
    search_value_parm = []

    for key, value in condition.items():
        search_value_parm.append(value)
        search_where_parm = search_where_parm + f" and {key}=%s"

    search_sql = ""

    if sqlchareter.find("where") != -1 or sqlchareter.find("Where") != -1 or sqlchareter.find("WHERE") != -1:
        search_sql = f" {sqlchareter} and {search_where_parm}  {orderBy}   {limit}"
    else:
        search_sql = f" {sqlchareter} where {search_where_parm}  {orderBy}  {limit}"

    # logger.debug(search_sql)

    with UsingMysql(log_time=True) as um:
        um.cursor.execute(search_sql, search_value_parm)
        result_data = um.cursor.fetchall()
        return result_data


# Fetch a record set by a complete SQL statement
def getRowsByFullSQl(sql):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        return result_data


# Query data by condition and pagination
def getRowsBySQlNoCondtion(sqlchareter, limit):
    # logger.debug(sqlchareter)
    sel_sql = f" {sqlchareter} limit {limit} "
    # logger.debug(sel_sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sel_sql)
        result_data = um.cursor.fetchall()
        return result_data


# Query a single row by SQL statement
def getRowsBySQlCase(sql):

    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchone()
        return result_data


# Query key/value pairs by SQL statement
def getSQL2KeyValueMap(sql, keyClm, valueClm):

    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}

        for item in result_data:
            key = item[keyClm]
            value = item[valueClm]
            result_map[key] = value

        return result_map


# Build a MAP keyed by primary key from one field, over a SQL result
def getSQL2Map4Value(sql, key, valuekey):

    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()

        result_map = {}
        for item in result_data:
            key1 = item[key]
            value1 = item[valuekey]
            result_map[key1] = value1

        return result_map


# Build a MAP keyed by primary key from one field, over a SQL result
def getSQL2Map(sql, key):

    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()

        result_map = {}
        for item in result_data:
            key1 = item[key]
            result_map[key1] = item

        return result_map


# Fetch an array of one field's values by SQL statement
def getSQL2Arr(sql, key):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()

        result_array = []
        for item in result_data:
            value = item[key]
            result_array.append(value)

        return result_array


# Group the SQL result by a given field: the field value becomes the key and the list of matching rows becomes the value
def getSQL2Map4Arr(sql, key):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}

        for item in result_data:
            value = item[key]
            temp_array = result_map.get(value)

            if temp_array is None:
                temp_array = []
                temp_array.append(item)
                result_map[value] = temp_array
            else:
                temp_array.append(item)
                result_map[value] = temp_array

        return result_map


# Group the SQL result by a given field: the field value becomes the key and the matching row becomes the value
def getSQL2Map4bean(sql, key):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}
        print(result_data)
        for item in result_data:
            value = item[key]
            result_map[value] = item

        return result_map


# Group the SQL result by a given field: the field value becomes the key and the list of matching rows becomes the value
def getSQL2Map4keyArr(sql, key, valuekey):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}

        for item in result_data:
            value = item[key]
            temp_array = result_map.get(value)

            if temp_array is None:
                temp_array = []
                temp_array.append(item[valuekey])
                result_map[value] = temp_array
            else:
                temp_array.append(item[valuekey])
                result_map[value] = temp_array

        return result_map


# Build a two-level map from two key fields and one value field over the SQL result
def getSQL2Map2Map(sql, key, childkey, valuekey):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}

        for item in result_data:
            key_value = item[key]
            child_key_value = item[childkey]
            valuekey_value = item[valuekey]

            temp_map = result_map.get(key_value)

            if temp_map is None:
                temp_map = {}
                temp_map[child_key_value] = valuekey_value
                result_map[key_value] = temp_map
            else:
                temp_map[child_key_value] = valuekey_value
                result_map[key_value] = temp_map

        return result_map


# Build a two-level map from two key fields and one value field over the SQL result
def getSQL2Map2Map4ayy(sql, key, childkey):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_map = {}

        for item in result_data:
            key_value = item[key]
            child_key_value = item[childkey]

            temp_map = result_map.get(key_value)

            if temp_map is None:
                temp_map = {}
                temp_map[child_key_value] = [item]
                result_map[key_value] = temp_map
            else:
                array_temp = temp_map.get(child_key_value)
                if array_temp is None:
                    array_temp = []
                    array_temp.append(item)
                    temp_map[child_key_value] = array_temp
                else:
                    array_temp.append(item)
                    temp_map[child_key_value] = array_temp

                result_map[key_value] = temp_map

        return result_map


# Extract an array of one field's values from the SQL result
def getSQL2KeyValueArr(sql, key, ifrepeat=False):
    # logger.debug(sql)
    with UsingMysql(log_time=True) as um:
        um.cursor.execute(sql)
        result_data = um.cursor.fetchall()
        result_array = []

        for item in result_data:
            value = item[key]

            if value is not None and value != "":
                if ifrepeat:
                    if value not in result_array:
                        result_array.append(value)
                else:
                    result_array.append(value)
        return result_array
