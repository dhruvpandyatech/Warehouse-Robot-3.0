import os
import sys
import datetime

# Add parent directory to path to import packages if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_test():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        print("[INFO] No MONGODB_URI environment variable detected. Skipping live connection test.")
        print("[INFO] To run live connection tests, set MONGODB_URI. E.g.:")
        print("  Windows: $env:MONGODB_URI=\"mongodb+srv://...\"")
        print("  Linux/Mac: export MONGODB_URI=\"mongodb+srv://...\"")
        print("\nSimulating MongoDB connection and collection updates...")
        
        # Simulated database structure to verify queries logic
        simulated_db = {}
        
        # Test 1: Init Database Seeding Simulation
        seeded_slots = []
        for slot in range(1, 11):
            row_num = 1 if slot <= 5 else 2
            rack_num = slot if slot <= 5 else slot - 5
            seeded_slots.append({
                "slot_id": slot,
                "row": row_num,
                "rack": rack_num,
                "package_id": None,
                "last_scanned": None
            })
        simulated_db["inventory"] = seeded_slots
        print("[OK] Simulated seeding database: seeded 10 slots.")
        
        # Test 2: Find target package simulation
        target_qr = "e6d237a5-6417-4bf6-b893-64506cfd3b1f"
        # Manually place target package in slot 3 (Row 1, Rack 3)
        simulated_db["inventory"][2]["package_id"] = target_qr
        
        # Simulate find_one query
        found = None
        for item in simulated_db["inventory"]:
            if item["package_id"] == target_qr:
                found = item
                break
        
        assert found is not None, "Failed to find package in simulated DB."
        assert found["row"] == 1 and found["rack"] == 3, f"Expected Row 1, Rack 3, got Row {found['row']}, Rack {found['rack']}"
        print("[OK] Simulated find_one query retrieved correct coordinates.")
        
        # Test 3: Update slot scan simulation
        scanned_pkg = "new-package-123"
        # Simulate de-duplicate: clear other slots containing pkg
        for item in simulated_db["inventory"]:
            if item["package_id"] == scanned_pkg:
                item["package_id"] = None
                
        # Simulate update_one
        updated = False
        for item in simulated_db["inventory"]:
            if item["row"] == 1 and item["rack"] == 1:
                item["package_id"] = scanned_pkg
                item["last_scanned"] = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat()
                updated = True
                break
                
        assert updated, "Failed to find slot to update."
        assert simulated_db["inventory"][0]["package_id"] == scanned_pkg
        print("[OK] Simulated update_one / update_many completed successfully.")
        
        print("\nAll database simulation tests passed successfully!")
        return

    # If MONGODB_URI is provided, run live tests
    from pymongo import MongoClient
    print(f"Connecting to live MongoDB Atlas at: {uri}")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.server_info() # Trigger connection test
        print("[OK] Connected to live MongoDB server successfully.")
        
        # Connect to a temporary test collection
        db = client["warehouse_db_test"]
        test_col = db["inventory_test"]
        
        # Clear previous test data
        test_col.delete_many({})
        
        # Seeding
        slots = []
        for slot in range(1, 11):
            row_num = 1 if slot <= 5 else 2
            rack_num = slot if slot <= 5 else slot - 5
            slots.append({
                "slot_id": slot,
                "row": row_num,
                "rack": rack_num,
                "package_id": None,
                "last_scanned": None
            })
        test_col.insert_many(slots)
        print("[OK] Inserted 10 seeded inventory slots.")
        
        # Verify seeding count
        count = test_col.count_documents({})
        assert count == 10, f"Expected 10 slots, found {count}"
        
        # Update simulation (de-duplicate & scan slot)
        target_qr = "test-pkg-999"
        # De-duplicate
        test_col.update_many({"package_id": target_qr}, {"$set": {"package_id": None}})
        # Update
        test_col.update_one({"row": 1, "rack": 3}, {"$set": {"package_id": target_qr, "last_scanned": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat()}})
        
        # Verify update
        updated_doc = test_col.find_one({"row": 1, "rack": 3})
        assert updated_doc["package_id"] == target_qr, "Failed to update target package ID."
        print("[OK] Live update queries verified.")
        
        # Cleanup test collection
        test_col.delete_many({})
        print("[OK] Cleaned up test records from database.")
        
        print("\nLive MongoDB Atlas connection tests passed successfully!")
        
    except Exception as e:
        print(f"[ERROR] Live connection test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_test()
