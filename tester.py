import time
import os
import base64
import pathlib
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

def run_tia_tester(html_file_path="index.html", target_address=""):
    if not os.path.exists(html_file_path):
        print(f"Error: Could not find {html_file_path}")
        return
        
    if not target_address.strip():
        print("Error: No address was provided. Exiting.")
        return

    # Properly format the file:// URL for Windows
    app_url = pathlib.Path(os.path.abspath(html_file_path)).as_uri()
    
    chrome_options = Options()
    # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--allow-file-access-from-files") # Helps with local HTML testing
    
    print("\nStarting Chrome WebDriver...")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    try:
        # 1. Open the App
        print(f"Opening app: {app_url}")
        driver.get(app_url)
        
        # Increased wait time to 15 seconds to account for local asset loading
        wait = WebDriverWait(driver, 15) 
        
        # 2. Handle the Login Gate
        print("Logging in...")
        
        # Ensure the element is not just visible, but interactable
        username_input = wait.until(EC.element_to_be_clickable((By.ID, "loginUser")))
        username_input.clear()
        username_input.send_keys("admin")
        
        password_input = driver.find_element(By.ID, "loginPassword")
        password_input.clear()
        password_input.send_keys("Packer4551")
        
        driver.find_element(By.CSS_SELECTOR, "#loginForm button[type='submit']").click()
        
        # Wait for the login modal to disappear
        wait.until(EC.invisibility_of_element_located((By.ID, "loginGate")))
        print("Login successful.")
        time.sleep(2) # Give the map/background scripts a moment to initialize
        
        # 3. Enter the User-Provided Address
        print(f"Testing address: {target_address}")
        search_input = wait.until(EC.element_to_be_clickable((By.ID, "quickTiaInput")))
        search_input.clear()
        search_input.send_keys(target_address)
        
        # 4. Click Search
        driver.find_element(By.ID, "quickTiaSearchBtn").click()
        print("Searching for location data...")
        
        # 5. Wait for the database results and Apply Data
        time.sleep(5) # Wait for autocomplete/search API
        
        try:
            use_calc_btn = driver.find_elements(By.ID, "quickTiaUseCalculatedBtn")
            if use_calc_btn and use_calc_btn[0].is_displayed():
                driver.execute_script("arguments[0].click();", use_calc_btn[0])
                print("Applied: 'Calculated Data'")
            else:
                exact_btns = driver.find_elements(By.XPATH, "//button[contains(@onclick, 'useExactTIAData')]")
                if exact_btns and exact_btns[0].is_displayed():
                    driver.execute_script("arguments[0].click();", exact_btns[0])
                    print("Applied: 'Exact Data'")
        except Exception as e:
            print("Note: Could not click apply data button automatically. Proceeding with default values.")
            
        time.sleep(2)
        
        # 6. Trigger Calculations
        print("Calculating all metrics...")
        calc_btn = wait.until(EC.element_to_be_clickable((By.ID, "calcBtn")))
        driver.execute_script("arguments[0].click();", calc_btn)
        
        time.sleep(5) # Wait for Chart.js rendering
        
        # 7. Generate Full Report
        print("Generating PDF Report...")
        driver.execute_script("document.body.classList.add('report-mode');")
        time.sleep(1)

        pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
            "landscape": False,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True
        })
        
        report_filename = f"TIA_Report_{int(time.time())}.pdf"
        with open(report_filename, "wb") as f:
            f.write(base64.b64decode(pdf_data['data']))
            
        print(f"✅ Success! Report saved as: {report_filename}")
        
    except Exception as e:
        print(f"❌ An error occurred during testing: {e}")
        # Debugging helper: Check if we are actually on the right page
        try:
            print(f"Current Browser URL was: {driver.current_url}")
            print(f"Page Title was: {driver.title}")
        except:
            pass
            
    finally:
        driver.quit()

if __name__ == "__main__":
    print("=== TIA Automated Tester ===")
    user_address = input("Please enter the address you want to test: ")
    
    run_tia_tester("index.html", user_address)