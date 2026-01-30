import yaml
import os, logging

class Config:
    def __init__(self):
        self.defaults = {
            'base_url': "https://attendance.caraga.nia.gov.ph",
            'auth_url': "https://accounts.nia.gov.ph/Account/Login",
            'enable_csv': False,
            # Reconnect / reauth settings
            'reconnect_max_attempts': 10,
            'reconnect_max_delay': 30,
            'reauth_timeout': 60,
            'enable_reauth': True
        }
        self.config_path = os.path.expanduser('~/.nia_monitor_config.yaml')
    
    def load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = yaml.safe_load(f) or {}
                    return {**self.defaults, **user_config}
            except Exception as e:
                logging.warning(f"│ CONFIG LOAD ERROR: {e}")
        return self.defaults.copy()
    
    def save(self, config_data):
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_data, f, default_flow_style=False)
        except Exception as e:
            logging.error(f"│ SAVE CONFIG ERROR: {e}")