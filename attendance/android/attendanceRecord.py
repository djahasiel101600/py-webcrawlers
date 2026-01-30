import re
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class AttendanceRecord:
    date_time: datetime
    temperature: Optional[float]
    employee_id: str
    employee_name: str
    machine_name: str
    status: str
    
    @classmethod
    def from_api_data(cls, api_record: Dict[str, Any]) -> 'AttendanceRecord':
        """Create record from API JSON data"""
        date_time = cls.parse_net_date(api_record['DateTimeStamp'])
        temperature = float(api_record['Temperature']) if api_record['Temperature'] else None
        
        # Determine status from AccessResult
        status = "ACCESS_GRANTED" if api_record['AccessResult'] == 1 else "ACCESS_DENIED"
        
        return cls(
            date_time=date_time,
            temperature=temperature,
            employee_id=api_record['EmployeeID'],
            employee_name=api_record['Name'],
            machine_name=api_record['MachineName'],
            status=status
        )
    
    @staticmethod
    def parse_net_date(net_date_string):
        """Convert .NET Date format to Python datetime"""
        match = re.search(r'\/Date\((\d+)\)\/', net_date_string)
        if match:
            timestamp = int(match.group(1))
            return datetime.fromtimestamp(timestamp / 1000)
        return datetime.now()
