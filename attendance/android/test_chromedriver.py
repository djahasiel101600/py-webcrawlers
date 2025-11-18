# test_chromedriver.py
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import logging

logging.basicConfig(level=logging.INFO)

def test_chromedriver():
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1280,720")
        
        # Set Chromium binary (install if not present)
        options.binary_location = "/usr/bin/chromium-browser"
        
        # Use the ChromeDriver we found
        service = Service("/usr/local/bin/chromedriver")
        
        print("Starting ChromeDriver test...")
        driver = webdriver.Chrome(service=service, options=options)
        
        print("Opening test page...")
        driver.get("https://httpbin.org/html")
        
        print(f"Page title: {driver.title}")
        print("✓ ChromeDriver test successful!")
        
        driver.quit()
        return True
        
    except Exception as e:
        print(f"❌ ChromeDriver test failed: {e}")
        return False

if __name__ == "__main__":
    test_chromedriver()