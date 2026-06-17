def none_or_zero(data):
    if data is None or len(data) == 0:
        return True
    return False

def none_check(data, default=""):
    if data==None :
        return default
    return data

from datetime import datetime
DEFAULT_DATE_FORMAT = "%Y-%m-%d"
def check_date_format(date_format: str, default_format=DEFAULT_DATE_FORMAT) -> str:
    #print(f"date_format={date_format}, default_format={default_format}")

    if not isinstance(date_format, str) or not date_format.strip() or date_format.strip().lower() == "none":
        #print(f"Invalid format (None, empty, or 'None'), using default_format={default_format}")
        return default_format

  # Basic token check (must contain % character)
    if '%' not in date_format:
        #print(f"Format does not contain any '%' tokens, using default_format={default_format}")
        return default_format

    try:
        base_date = datetime(2000, 1, 1)
        formatted = base_date.strftime(date_format)
        parsed_date = datetime.strptime(formatted, date_format)
    # If the date is parsed correctly, both values must match
        if base_date != parsed_date:
            #print(f"Parsed date does not match the base date, using default_format={default_format}")
            return default_format
        #print(f"Valid format: date_format={date_format}, formatted={formatted}")
        return date_format
    except (ValueError, TypeError):
        #print(f"Invalid format, using default_format={default_format}")
        return default_format
