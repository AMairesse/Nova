from datetime import datetime, date, timedelta, timezone


METADATA = {
    'name': 'Date / Time',
    'description': 'Manipulate dates and times',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}

def current_date() -> str:
    return date.today().strftime('%Y-%m-%d')

def current_datetime() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def add_days(date: str, days: int) -> str:
    start_date = datetime.strptime(date, '%Y-%m-%d')
    end_date = start_date + timedelta(days=days)
    end_date_str = end_date.strftime('%Y-%m-%d')
    return end_date_str

def add_weeks(date: str, weeks: int) -> str:
    start_date = datetime.strptime(date, '%Y-%m-%d')
    end_date = start_date + timedelta(weeks=weeks)
    end_date_str = end_date.strftime('%Y-%m-%d')
    return end_date_str

def count_days(start_date: str, end_date: str) -> int:
    start_date = datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.strptime(end_date, '%Y-%m-%d')
    delta = end_date - start_date
    return delta.days

def get_functions():
    return {
        "current_date": {
            "callable": current_date,
            "description": "Return the current date (format: YYYY-MM-DD)",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "current_datetime": {
            "callable": current_datetime,
            "description": "Return the current date and time (format: YYYY-MM-DD HH:MM:SS)",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        "add_days": {
            "callable": add_days,
            "description": "Add N days to the provided date",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "the date to add days to (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    },
                    "days": {
                        "type": "integer",
                        "description": "number of days to add (can be negative)"
                    }
                },
                "required": ["date", "days_ahead"]
            }
        },
        "add_weeks": {
            "callable": add_weeks,
            "description": "Add N weeks to the provided date",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "the date to add weeks to (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    },
                    "weeks": {
                        "type": "integer",
                        "description": "number of weeks to add (can be negative)"
                    }
                },
                "required": ["date", "weeks_ahead"]
            }
        },
        "count_days": {
            "callable": count_days,
            "description": "Count the number of days between two dates",
            "input_schema": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "the start date (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "the end date (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        },
    }