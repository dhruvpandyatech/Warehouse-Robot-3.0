import math
import sys
import os

# Add parent directory to path so we can import robot_agent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from robot_agent import get_grid_location, get_slot_coordinates, solve_tsp

def test_coordinate_mapping():
    print("Testing coordinate mapping...")
    
    # Test centers
    # Row 1, Rack 1 -> (0.2, 0.5)
    row, rack = get_grid_location(0.2, 0.5)
    assert (row, rack) == (1, 1), f"Expected (1, 1), got ({row}, {rack})"
    
    # Row 2, Rack 5 -> (1.8, 1.5)
    row, rack = get_grid_location(1.8, 1.5)
    assert (row, rack) == (2, 5), f"Expected (2, 5), got ({row}, {rack})"
    
    # Test boundaries and slight noise
    # x=1.35, y=0.4 -> should map to Row 1, Rack 4 (center is 1.4, 0.5)
    row, rack = get_grid_location(1.35, 0.4)
    assert (row, rack) == (1, 4), f"Expected (1, 4), got ({row}, {rack})"
    
    # Reverse mapping
    x, y = get_slot_coordinates(1, 4)
    assert math.isclose(x, 1.4) and math.isclose(y, 0.5), f"Expected (1.4, 0.5), got ({x}, {y})"
    
    print("[OK] Coordinate mapping tests passed.")

def test_tsp_solver():
    print("Testing TSP shortest-path solver...")
    
    start_x, start_y = 0.0, 0.0
    
    # Suppose we need to scan slots: (1, 3), (1, 1), (1, 5)
    # Physically at: (1.0, 0.5), (0.2, 0.5), (1.8, 0.5)
    # Optimal path from (0.0, 0.0) should visit Slot 1 first (0.2, 0.5), then Slot 3 (1.0, 0.5), then Slot 5 (1.8, 0.5).
    slots = [(1, 3), (1, 1), (1, 5)]
    path = solve_tsp(start_x, start_y, slots)
    assert path == [(1, 1), (1, 3), (1, 5)], f"Expected [(1, 1), (1, 3), (1, 5)], got {path}"
    
    # Test path starting from a middle position (1.0, 0.5) to visit (1, 2) and (1, 4)
    path_middle = solve_tsp(1.0, 0.5, [(1, 4), (1, 2)])
    # From 1.0, both 0.6 and 1.4 are at equal distance (0.4m). Any of the two is optimal.
    assert len(path_middle) == 2
    
    # Test path across rows
    # Start at (1.8, 1.5) [Row 2, Rack 5]
    # Remaining slots: (1, 5) [Row 1, Rack 5], (1, 4) [Row 1, Rack 4], (2, 4) [Row 2, Rack 4]
    # Coordinates: 
    # (1.8, 1.5) -> (2, 5)
    # Target 1: (2, 4) @ (1.4, 1.5) -> dist = 0.4
    # Target 2: (1, 5) @ (1.8, 0.5) -> dist = 1.0
    # Let's see the solver's path:
    cross_slots = [(1, 5), (1, 4), (2, 4)]
    path_cross = solve_tsp(1.8, 1.5, cross_slots)
    
    # Optimal should be: (2, 4) -> (1, 4) -> (1, 5) or (1, 5) -> (1, 4) -> (2, 4)?
    # Let's check distances:
    # Option A: (1.8,1.5) -> (2,4) dist 0.4 -> (1,4) dist 1.0 -> (1,5) dist 0.4. Total = 1.8
    # Option B: (1.8,1.5) -> (1,5) dist 1.0 -> (1,4) dist 0.4 -> (2,4) dist 1.0. Total = 2.4
    assert path_cross == [(2, 4), (1, 4), (1, 5)], f"Expected [(2, 4), (1, 4), (1, 5)], got {path_cross}"
    
    print("[OK] TSP solver tests passed.")

if __name__ == "__main__":
    test_coordinate_mapping()
    test_tsp_solver()
    print("All algorithm tests succeeded successfully.")
