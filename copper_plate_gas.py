# Imported Packages 
import cvxpy as cp
import numpy as np
import gurobipy
import csv
import pandas as pd
import time
import matplotlib.pyplot as plt
import re
import math
import os
from ratio_wind_sun import rw, total_r, total_r_SE, total_r_SECons # Importing the ratio for wind to sun for each node, and total renewables

# np.set_printoptions(threshold=np.inf)
Nt = int(24*28) # Number of time-intervals
dt = 1 # Length of Time Interval in hours 
time_interval_str = str(Nt)  # String of time-interval 

location = "LongBeach" # Savannah, LongBeach, Houston

subsidy_types = {"solar": False, "truck": False, "charger": False, "weight_truck": True, "carbon": True} # dictionary to indicate which subsidies are active (and penalties for carbon)

# Path to Product File
product_type = "cement"
product_path = location + '_products/' + product_type + '/'
network_type = "Small7" # Texas7, 7, Small7, SmallEast, SmallerEast
network_path = location +'_supply_network/' + network_type + '/'
# demand, demandSE have demand at the end, and demandCons and demandSECons have constant demand throughout
# demandWeekly, demandBiWeekly, demandMonthly, demandSEWeekly, demandSEBiWeekly, demandSEMonthly

demand_network_type = "demandMonthly"  
offset = 0

buy_back_time = 4.5 # years for financial buyback period
manufacturer = "Nikola" # Tesla or Nikola

intensity_gas = 10.18 # kg CO2 per gallon for diesel fuel (https://www.eia.gov/environment/emissions/co2_vol_mass.php?)
intensity_elec_dict = {
    "Savannah": 0.382 ,  # kg CO2 per kWh for electricity in Georgia (https://www.epa.gov/egrid/summary-data)
    "Houston": 0.3329, # kg CO2 per kWh for electricity in Texas (https://www.epa.gov/egrid/summary-data)
    "LongBeach": 0.1944 # kg CO2 per kWh for electricity in California (-)
}
intensity_elec = intensity_elec_dict[location] # kg CO₂/kWh # kg CO2 per kWh for electricity in Georgia (https://www.epa.gov/egrid/summary-data)
carbon_price = 0.05 # $/kg CO2 penalty for non-renewable electricity and gas usage 

# files: parameters, imports, demand
'''
PARAMETERS THAT ARE HARD-CODED (NOT PRODUCT SPECIFIC)
'''
# -----------------------
# Truck Parameters
# -----------------------
truck_lifetime = 10 # years

# Payload capacities (kg)
if subsidy_types["weight_truck"] == False:
    truck_load_elec = 24287
else: 
    truck_load_elec = 26286

truck_load_gas = 28349   

# https://ops.fhwa.dot.gov/freight/pol_plng_finance/policy/fastact/tswprovisions2019/index.htm
# https://www.ccjdigital.com/regulations/article/15306841/coalition-calls-for-increased-weight-limits-for-auto-haulers

# Truck weights (kg)
truck_weight_elec = 12000  # unloaded electric truck weight
truck_weight_gas = 7938    # unloaded gas truck weight
# Diesel consumption per mile
gallon_per_mile = 0.1  # gas truck fuel consumption (~10 mpg)

# av_speed = 60  # average speed in mph

# Fuel cost ($/gallon) 
diesel_cost = {
    "Savannah": 3.468,
    "LongBeach": 5.060,
    "Houston": 3.106
}
price_per_gallon = diesel_cost[location]  # $/gallon (Georgia Diesel Price)

# Ratios of unloaded to loaded truck weight (used for efficiency or cost calculations)
theta_elec = truck_weight_elec / (truck_weight_elec + truck_load_elec)
theta_gas = truck_weight_gas / (truck_weight_gas + truck_load_gas)

# Maintenance costs ($/mile)
c_maintenance_elec = 0.101
c_maintenance_gas = 0.601 

# Buyback periods (years) - financial horizon for investment recovery
buyback_period_truck_elec = buy_back_time
buyback_period_truck_gas = buy_back_time

# Annualized CAPEX (distributed hourly over the buyback period)
c_truck_gas = 140000 * (Nt * dt) / (24 * 365.25 * buyback_period_truck_gas)

# Annualized subsidy on truck CAPEX (also amortized over buyback period)
c_subsidy_truck = (
    40000 * (Nt * dt) / (24 * 365.25 * buyback_period_truck_elec)
    if subsidy_types["truck"] else 0
)

# -----------------------
# Warehouse (Storage) Parameters
# -----------------------
store_lifetime = 30  # years (technical lifetime)

# -----------------------
# Charger Parameters
# -----------------------
charger_lifetime = 10  # years (technical lifetime)

# Financial buyback period for chargers
buyback_period_charger = buy_back_time


# Annualized CAPEX and subsidy amortized over buyback period
# Annual maintenance cost per charger
maintenance_charger_yearly = 1000

# Convert to model time horizon cost
maintenance_charger = maintenance_charger_yearly * (Nt * dt) / (24 * 365.25)

# Capital cost per charger amortized over the buyback period (Includes equipment and installation)
capital_cost_charger = 100000 * (Nt * dt) / (24 * 365.25 * buyback_period_charger)

# Final total cost per charger including maintenance
c_charger = capital_cost_charger + maintenance_charger

c_subsidy_charger = (15000 * (Nt * dt) / (24 * 365.25 * buyback_period_charger)) if subsidy_types["charger"] else 0

charge_efficiency = 0.9
charging_speed = 450  # kW per charging unit

# -----------------------
# Behind-the-meter Solar Parameters
# -----------------------
solar_lifetime = 25  # years (technical lifetime)

cost_solar = 3.13 * 1000  # $/kW installed

# buyback period for solar investments
buyback_period_solar = buy_back_time


# Annualized solar CAPEX amortized over buyback period
k_solar = cost_solar * (Nt * dt) / (24 * 365.25 * buyback_period_solar) 

c_subsidy_solar = (k_solar * 0.6) if subsidy_types["solar"] else 0  # 60% subsidy on solar CAPEX

# -----------------------
# Driver Cost
# -----------------------``
c_driver = 0.5  # $/mile corresponding roughly to $30/hr wage and 60 mph speed

buyback_period_battery = buy_back_time
#------------------------
# Battery Cost
# ------------------------
c_battery = 100000 * (Nt * dt) / (24 * 365.25 * buyback_period_battery)

#------------------------
# Penalty of Unmet Demand
# ------------------------
c_unmet =  0.1 # $/kg of unmet demand

'''
Vehicle Parameters
'''
# Read the CSV file
df = pd.read_csv("Truck_Overview.csv")

# Filter and get the row
row = df[df["Manufacturer"] == manufacturer]

# Access individual fields (optional)
if not row.empty:
    avg_cost = int(row["Average Cost"].values[0]) 
    truck_batt = row["Battery Size (kWh) "].values[0] # 900 kWh (Tesla) battery capacity for electric trucks 
    driving_range = row["Driving Range (miles)"].values[0]
    kWh_per_mile = row["kWh/mile"].values[0]
else:
    print(f"{manufacturer} not found in the dataset.")

c_truck_elec = avg_cost * (Nt * dt) / (24 * 365.25 * buyback_period_truck_elec)
'''
NETWORK SPECIFIC PARAMETERS
'''
n = 0
nodes = {}
iNodes = {}

### Get Set of Nodes (Cities)
with open(network_path+'cities.csv','r') as csvfile:
    reader = csv.reader(csvfile)
    next(reader)
    for row in reader:
        nodes[row[0]] = n
        iNodes[n] = row[0]
        n += 1

### Get Links between Nodes 
transport_links = [] # origin, destination, distance
Eij = [] # energy to get from one city to another
Gij = [] # Gas to get from one city to another 

with open(network_path+'links.csv','r') as csvfile:
    reader = csv.reader(csvfile)
    next(reader)
    for row in reader:
        distance = float(row[2])
        average_speed = float(row[3]) * 0.7
        transport_links.append([nodes[row[0]],nodes[row[1]], distance, average_speed]) #
        Eij.append(distance*kWh_per_mile)
        Gij.append(distance*gallon_per_mile)
Eij = np.array((Eij*2)*Nt)
Gij = np.array((Gij*2)*Nt)

Ni = len(nodes) # number of nodes
Nj = len(transport_links) # number of transport links 

'''
PRODUCT SPECIFIC PARAMETERS
'''
with open(product_path+'params.csv','r') as csvfile:
    reader = csv.reader(csvfile)
    product_name = next(reader)[1]
    product_density = float(next(reader)[1])
    cost_per_acre_warehouse = float(next(reader)[1])

# 1 acre = 4046.86 m2 assume 10m height gives 40468.2 m3 per acre for paper and 45994.48 for cement 

product_per_acre = 45894.48*product_density # kg
cost_per_product = (cost_per_acre_warehouse)/product_per_acre # cost per kg

buyback_period_space = buy_back_time
c_space = np.array([cost_per_product*(Nt*dt)/(24*365.25*buyback_period_space)]*Ni) # CAPEX of unit

'''
SUPPLY PARAMETERS
'''
total_demand = 0
total_imports = 0
int_gap = {'h':int(1/dt),'d':int(24/dt),'w':int(24*7/dt),'m':int(24*30/dt)} # represents different time intervals, "h" for hour, "d" for day, "w" for week", and "m" for month. 
s = np.zeros((Ni*Nt)) # imports +tive, demand -tive
with open(product_path+'imports.csv','r') as csvfile:
    reader = csv.reader(csvfile)
    head1 = next(reader)
    head2 = next(reader)
    import_locs = head2[1:] #Import Location (Cities)
    for row in reader:
        if (int(row[0])-1)*int_gap[head1[1]] >= Nt:
            continue
        for i in range(len(import_locs)):
            if import_locs[i] == '':
                continue
            for t in range((int(row[0])-1)*int_gap[head1[1]],int(row[0])*int_gap[head1[1]]):
                if t < Nt:
                    s[nodes[import_locs[i]]+t*Ni] += float(row[1+i])/int_gap[head1[1]]
                    total_imports += float(row[1+i])/int_gap[head1[1]]
                                                                  
with open(product_path + demand_network_type + '.csv','r') as csvfile:
    reader = csv.reader(csvfile)
    head1 = next(reader)
    head2 = next(reader)
    demand_locs = head2[1:]
    for row in reader:
        if (int(row[0])-1)*int_gap[head1[1]] >= Nt: # calculates the number of intervals of size dt based on monthly, weekly, or daily                                 
            continue
        for i in range(len(demand_locs)):
            if demand_locs[i] == '':
                continue
            for t in range((int(row[0])-1)*int_gap[head1[1]],int(row[0])*int_gap[head1[1]]):       
                if t < Nt:
                    s[nodes[demand_locs[i]]+t*Ni] -= float(row[1+i])/int_gap[head1[1]]
                    total_demand += float(row[1+i])/int_gap[head1[1]]

'''
POWER PARAMETERS
'''
# Only takes the nodes that are in the current system from rw that contains all nodes
'''
ratio_wind = {node: rw[node] for node in nodes if node in rw}  

total_ren = {node: total_r[node] for node in nodes if node in total_r} #total_r_SE

wind_cf = {node: [] for node in nodes}

for node in nodes:
    with open('data/Solar_' + node + '.csv','r') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)
        for row in reader:
            solar_cf[node].append(float(row[1]))

    with open('data/Wind_' + node + '.csv','r') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)
        for row in reader:
            wind_cf[node].append(float(row[1]))

rSolar_ = {node: [] for node in nodes}
rWind_ = {node: []  for node in nodes}
for node in nodes:
    for t in range(Nt*dt):
        #print(solar_cf[node][t+offset])
        rSolar_[node] += [(1 - ratio_wind[node])*solar_cf[node][t+offset]]
        rWind_[node] += [ratio_wind[node]*wind_cf[node][t+offset]]

for node in nodes:
    rSolar_[node] = np.array(rSolar_[node])
    rSolar_[node] = rSolar_[node] * ((1 - ratio_wind[node])*total_ren[node])
    rWind_[node] = np.array(rWind_[node])
    rWind_[node] = rWind_[node] * (ratio_wind[node] * total_ren[node])

r_S_down = np.zeros(Nt)
r_W_down = np.zeros(Nt)
for node in nodes:
    for t in range(Nt):
        for t2 in range(dt):
            r_S_down[t] += rSolar_[node][t*dt+t2]/dt
            r_W_down[t] += rWind_[node][t*dt+t2]/dt
            
rWind = r_W_down 
rSolar = r_S_down 
'''
# Load solar capacity factors per node
solar_cf = {node: [] for node in nodes}

for node in nodes:
    with open(location + '_data/Solar_' + node + '.csv','r') as csvfile:
        reader = csv.reader(csvfile)
        next(reader)
        for row in reader:
            solar_cf[node].append(float(row[1]))

# Normalize solar generation profile (1 unit of capacity)
rSolar_ = {node: [] for node in nodes}
for node in nodes:
    for t in range(Nt*dt):
        rSolar_[node] += [solar_cf[node][t+offset]]
    rSolar_[node] = np.array(rSolar_[node])

# Aggregate solar CF to match model time resolution (e.g., hourly from sub-hourly)
r_Solar = np.zeros((Ni, Nt))  # Shape (Ni, Nt) for all nodes and time steps

# Populate the array for each node
for node_idx, node in enumerate(nodes):
    for t in range(Nt):
        for t2 in range(dt):
            r_Solar[node_idx, t] += rSolar_[node][t * dt + t2] / dt




'''
SETTING UP SOME MATRICIES TO SPEED UP CALCULATIONS
'''
Depart = np.zeros((Ni*Nt,Nj*2*Nt)) # matrix to map departing trucks to locations
Arrive = np.zeros((Ni*Nt,Nj*2*Nt)) # matrix to map arriving trucks to locations

length_trip={}
for j in range(Nj):
    i1 = transport_links[j][0] # Extracts the index of the origin node for the current trasport link 
    i2 = transport_links[j][1] # Extracts the index of the destination node for the current trasport link 
    k = max(int(round(transport_links[j][2]/(transport_links[j][3]*dt))),1) # The number of time intervals needed to cover the distance bewteen the two cities where av_speed*dt is the distance traveled in one time interval 
    length_trip[j] = k
    length_trip[j+Nj] = k
    
    for t in range(Nt):
        Depart[i1+t*Ni,j+t*Nj*2] = 1.0 
        Depart[i2+t*Ni,j+Nj+t*Nj*2] = 1.0
        if t+k < Nt:
            Arrive[i2+(t+k)*Ni,j+t*Nj*2] = 1.0
            Arrive[i1+(t+k)*Ni,j+Nj+t*Nj*2] = 1.0

# Matrix update - difference between current and previous timestep - ignore first step
Delta = np.diag([0.]*Ni+[1.]*(Ni*(Nt-1)))
for i in range(Ni):
    for t in range(1,Nt):
        Delta[i+t*Ni,i+(t-1)*Ni] = -1.

CopyTime = np.zeros((Ni*Nt,Ni)) # Matrix to copy over time
SumNode = np.zeros((Nt,Ni*Nt)) # Matrices to sum over node or link
SumLink = np.zeros((Nt,Nj*Nt*2))
SumDriving = np.zeros((Nt,Nj*Nt*2))

for i in range(Ni):
    for t in range(Nt):
        CopyTime[i+t*Ni,i] = 1.0
        SumNode[t,i+t*Ni] = 1.0
for j in range(Nj*2):
    for t in range(Nt):
        SumLink[t,j+t*Nj*2] = 1.0 #Nj times 2?

        SumDriving[t,j+t*Nj*2] = 1.0
        
        for k in range(1,length_trip[j]):
            if t+k < Nt:
                SumDriving[t+k,j+t*Nj*2] = 1.0

'''
Utility Rates
'''
# Read in  rates CSV
file_path = f"utility/{location}_utility_rate.csv"
rates_df = pd.read_csv(file_path)

if location == "Savannah":
    super_charge = rates_df.loc[0, "super_charge"]
    super_charge_array = np.ones((Ni, Nt)) * super_charge

    # Extract values from the first (and only) row
    on_peak_charge = rates_df.loc[0, "on_peak_charge"]
    off_peak_charge = rates_df.loc[0, "off_peak_charge"]
    demand_charge = rates_df.loc[0, "demand_charge"]
    fic_charge = rates_df.loc[0, "fic_charge"]
    flat_charge = rates_df.loc[0, "flat_charge"] 

    # Array for on-peak and off-peak Arrays 
    on_peak_array = np.zeros(Nt)
    off_peak_array = np.zeros(Nt)
    fic_charger_array = np.tile(np.ones(Nt) * fic_charge, (Ni, 1)) # Array for feed-in credit

    # Set summer TOU prices (on-peak and off-peak) for each hour
    for t in range(Nt):
        day_of_week = (t // 24) % 7  # 0=Mon,...,6=Sun
        hour_of_day = t % 24

        if (day_of_week < 5) and (14 <= hour_of_day <= 18):  # Mon-Fri 2-7pm on-peak summer
            on_peak_array[t] = on_peak_charge
            off_peak_array[t] = 0
        else:
            on_peak_array[t] = 0
            off_peak_array[t] = off_peak_charge

    # Now calculate weighted average price for each hour
    # 1/3 of year summer TOU prices + 2/3 off-peak price (non-summer all off-peak)
    weighted_charge_array = (1/3) * (on_peak_array + off_peak_array) + (2/3) * off_peak_charge
    

elif location == "LongBeach":
    # --- Energy charges ---
    super_charge = rates_df.loc[0, "super_charge"]
    super_charge_array = np.ones((Ni, Nt)) * super_charge 
    flat_charge = rates_df.loc[0, "flat_charge"]

    base_summer_charge = rates_df.loc[0, "base_summer_charge"]
    low_peak_summer_charge = rates_df.loc[0, "low_peak_summer_charge"]
    high_peak_summer_charge = rates_df.loc[0, "high_peak_summer_charge"]

    base_non_summer_charge = rates_df.loc[0, "base_non_summer_charge"]
    low_peak_non_summer_charge = rates_df.loc[0, "low_peak_non_summer_charge"]
    high_peak_non_summer_charge = rates_df.loc[0, "high_peak_non_summer_charge"]

    # --- Demand charges ---
    base_demand_summer_charge = rates_df.loc[0, "base_demand_summer_charge"]
    low_peak_demand_summer_charge = rates_df.loc[0, "low_peak_demand_summer_charge"]
    high_peak_demand_summer_charge = rates_df.loc[0, "high_peak_demand_summer_charge"]

    base_demand_non_summer_charge = rates_df.loc[0, "base_demand_non_summer_charge"]
    low_peak_demand_non_summer_charge = rates_df.loc[0, "low_peak_demand_non_summer_charge"]
    high_peak_demand_non_summer_charge = rates_df.loc[0, "high_peak_demand_non_summer_charge"]
    
    fic_charge = rates_df.loc[0, "fic_charge"]

    # Initialize arrays
    base_array = np.zeros(Nt)
    low_peak_array = np.zeros(Nt)
    high_peak_array = np.zeros(Nt)

    base_demand_array = np.zeros(Nt)
    low_peak_demand_array = np.zeros(Nt)
    high_peak_demand_array = np.zeros(Nt)

    fic_charger_array = np.tile(np.ones(Nt) * fic_charge, (Ni, 1))

    for t in range(Nt):
        day_of_week = (t // 24) % 7  # 0=Mon,...,6=Sun
        hour_of_day = t % 24

        if day_of_week < 5:  # Weekdays
            if 10 <= hour_of_day < 13 or 17 <= hour_of_day < 20:
                low_peak_array[t]  = (1/3) * low_peak_summer_charge + (2/3) * low_peak_non_summer_charge
                low_peak_demand_array[t] = (1/3) * low_peak_demand_summer_charge + (2/3) * low_peak_demand_non_summer_charge
        
            elif 13 <= hour_of_day < 17:
                high_peak_array[t] = (1/3) * high_peak_summer_charge + (2/3) * high_peak_non_summer_charge
                high_peak_demand_array[t] = (1/3) * high_peak_demand_summer_charge + (2/3) * high_peak_demand_non_summer_charge
            else:
                base_array[t] = (1/3) * base_summer_charge + (2/3) * base_non_summer_charge
                base_demand_array[t] = (1/3) * base_demand_summer_charge + (2/3) * base_demand_non_summer_charge
    
        else:  # Weekend
            base_array[t] = (1/3) * base_summer_charge + (2/3) * base_non_summer_charge
            base_demand_array[t] = (1/3) * base_demand_summer_charge + (2/3) * base_demand_non_summer_charge

                
elif location == "Houston":
    super_charge = rates_df.loc[0, "super_charge"]
    super_charge_array = np.ones((Ni, Nt)) * super_charge
    # Extract values from the first (and only) row
    energy_charge = rates_df.loc[0, "energy_charge"]
    demand_charge = rates_df.loc[0, "demand_charge"]
    fic_charge = rates_df.loc[0, "fic_charge"]
    flat_charge = rates_df.loc[0, "flat_charge"]   

    # Array for on-peak and off-peak Arrays 
    energy_charge_array = np.ones(Nt)*energy_charge
    fic_charger_array = np.tile(np.ones(Nt) * fic_charge, (Ni, 1))

else:
    print("Error: Location not recognized.")

'''
DECISION VARIABLES
'''
# Number of Trucks
y_elec = cp.Variable(Nj*2*Nt)# integer = True) # factor 2 for arriving and departing
y_gas = cp.Variable(Nj*2*Nt)# integer = True ) 

# Load of Trucks 
L_elec = cp.Variable(Nj*2*Nt)
L_gas  = cp.Variable(Nj*2*Nt)

# Stationary Trucks (Electric and Gas)
Y_elec = cp.Variable(Ni*Nt)
Y_gas = cp.Variable(Ni*Nt)

# Total Fleet (Electric and Gas)
Ytotal_elec = cp.Variable()
Ytotal_gas = cp.Variable()

# Stored Goods
x = cp.Variable(Ni*Nt)

q = cp.Variable (Ni*Nt)  # product/demand not delivered (undelivered products)

# Storage Size
W = cp.Variable(Ni)

# Stationary Truck Charge
C = cp.Variable(Ni*Nt)

# Charging Power
p = cp.Variable(Ni*Nt)

# Charging power from chargers being installed 
p_new = cp.Variable(Ni*Nt)

# Charging power from exisitng infrastucture 
p_existing = cp.Variable(Ni*Nt)

# Non-renewable Power
z = cp.Variable(Ni*Nt)

# Non-renewable Power from chargers being installed
z_new = cp.Variable(Ni*Nt)

# Non-renewable Power from existing infrastructure
z_existing = cp.Variable(Ni*Nt)

# Curtailed Power
curt = cp.Variable(Ni*Nt)

# Number of Chargers
N_charger = cp.Variable(Ni) # No. of chargers per location 

# Number of Batteries per location
B = cp.Variable(Ni) 

# Solar nameplate capacity (kWh) per node
Solar_Max = cp.Variable(Ni)

Solar_Max_reshaped = cp.reshape(Solar_Max, (Ni, 1))  # (Ni,1)
solar_generation = cp.multiply(Solar_Max_reshaped, r_Solar)  # elementwise multiply, shape (7, 672)    # (Ni,Nt) elementwise multiplication

# Binary Install Charger
install_binary = np.ones(Ni)
#install_binary = cp.Variable(Ni, boolean = True)

# create 2D “views” for constraints
y_elec_mat     = cp.reshape(y_elec,     (Nj*2, Nt))  # shape: (links, time)
y_gas_mat      = cp.reshape(y_gas,      (Nj*2, Nt))  # shape: (links, time)
Y_elec_mat     = cp.reshape(Y_elec,     (Ni, Nt))  # shape: (location, time)
Y_gas_mat      = cp.reshape(Y_gas,      (Ni, Nt))  # shape: (location, time)
p_mat          = cp.reshape(p,          (Ni, Nt))  # shape: (location, time)
p_new_mat      = cp.reshape(p_new,      (Ni, Nt))
p_existing_mat = cp.reshape(p_existing, (Ni, Nt))
z_mat          = cp.reshape(z,          (Ni, Nt))  # shape: (location, time)
z_new_mat      = cp.reshape(z_new,      (Ni, Nt))
z_existing_mat = cp.reshape(z_existing, (Ni, Nt))
curt_mat       = cp.reshape(curt,       (Ni, Nt))


'''
print("CopyTime", CopyTime.shape)
print("SolarMax", Solar_Max.shape)
print("Solar_Max_t",Solar_Max_reshaped.shape)
print("r_Solar", r_Solar.shape)
print("SumNode@p",(SumNode@p).shape)
print("p", p.shape)
print(solar_generation.shape)
'''
# Maintenance and driver cost total
driver_cost_total = cp.sum([
    c_driver * transport_links[j][2] * (
        y_elec[j + t * Nj * 2] +  # full electric
        y_gas[j + t * Nj * 2]     # full gas
    )
    for j in range(Nj)
    for t in range(Nt)
])

maintenance_cost_total = cp.sum([
    transport_links[j][2] * (
        c_maintenance_elec * y_elec[j + t * Nj * 2] +
        c_maintenance_gas * y_gas[j + t * Nj * 2]
    )
    for j in range(Nj)
    for t in range(Nt)
])
# -----------------------------------------------------------#
# Driver cost for electric and gas trucks separately
# -----------------------------------------------------------#
electric_driver_cost = cp.sum([
    c_driver * transport_links[j][2] * y_elec[j + t * Nj * 2]
    for j in range(Nj)
    for t in range(Nt)
])

gas_driver_cost = cp.sum([
    c_driver * transport_links[j][2] * y_gas[j + t * Nj * 2]
    for j in range(Nj)
    for t in range(Nt)
])
# -----------------------------------------------------------#

# -----------------------------------------------------------#
# Maintenance cost for electric and gas trucks separately
# -----------------------------------------------------------#
electric_maintenance_cost = cp.sum([
    c_maintenance_elec * transport_links[j][2] * y_elec[j + t * Nj * 2]
    for j in range(Nj)
    for t in range(Nt)
])

gas_maintenance_cost = cp.sum([
    c_maintenance_gas * transport_links[j][2] * y_gas[j + t * Nj * 2]
    for j in range(Nj)
    for t in range(Nt)
])
# -----------------------------------------------------------#

constraints = [Delta@Y_elec == Arrive@y_elec - Depart@y_elec,
               Delta@Y_gas == Arrive@y_gas - Depart@y_gas,
               Delta@x <= s + Arrive@L_elec - Depart@L_elec + Arrive@L_gas - Depart@L_gas + q,
               Delta@C ==  charge_efficiency*p*dt 
                 - Depart @ (np.diag(Eij*theta_elec)@y_elec) 
                 - Depart @ (np.diag(Eij*(1 - theta_elec))@(L_elec / truck_load_elec)) ,
               C <= (Y_elec + CopyTime@B) * truck_batt,
               Delta @ C <= (truck_batt * dt / 2) * Y_elec 
                 - Depart @ (np.diag(Eij*theta_elec)@y_elec) 
                 - Depart @ (np.diag(Eij*(1 - theta_elec))@(L_elec / truck_load_elec)) ,
               SumNode@Y_elec + SumDriving@y_elec <= Ytotal_elec, 
               SumNode@Y_gas + SumDriving@y_gas <= Ytotal_gas,
               x <= CopyTime@W, 
               L_elec <= truck_load_elec*y_elec,
               L_gas <= truck_load_gas*y_gas,
               x[:Ni] == x[-Ni:], 
               C[:Ni] == C[-Ni:],
               W >= 0,
               x >= 0,
               y_elec >= 0,
               y_gas >= 0,
               z_new >= 0,
               z_existing >= 0,
               Y_elec >= 0,
               Y_gas >= 0,
               L_elec >= 0,
               L_gas >= 0, 
               C >= 0,
               p_new >= 0,
               p_existing >= 0,
               B >= 0, 
               q >= 0
] 

M = 1e12  # You can adjust this bound based on practical system limits

constraints += [
    # Charger constraints
    N_charger >= 0,
    N_charger <= 1000*install_binary,

    # Solar constraints (solar only if install_binary is 1)
    Solar_Max >= 0,
    solar_generation >= 0,
    Solar_Max <= charging_speed * N_charger,
    Solar_Max <= M * install_binary,  # disables solar if no charger
    # cp.reshape(solar_generation, (Ni * Nt,))<= M * (CopyTime @ install_binary),
    solar_generation <= M * install_binary[:, None],

    # Curtailment logic
    cp.reshape(curt, (Ni, Nt)) >= 0,
    curt >= 0,
    cp.reshape(curt, (Ni, Nt)) <= M * install_binary[:, None],
    # curt <= M * (CopyTime @ install_binary),  # forces curt = 0 if install_binary = 0
    cp.reshape(curt, (Ni, Nt)) <= solar_generation,
    # cp.reshape(curt, (Ni, Nt)) <= cp.multiply(Solar_Max[:, None], r_Solar),
    

    # Power demand constraints
    p_new + cp.reshape(solar_generation, (Ni*Nt,)) - curt <= CopyTime @ (N_charger * charging_speed),
    p_new <= M * (CopyTime @ install_binary),
    
    # Power balance
    cp.reshape(p_new,(Ni, Nt)) == cp.reshape(z_new,(Ni, Nt)) - cp.reshape(curt, (Ni, Nt)) + cp.multiply(Solar_Max[:, None], r_Solar),  # if solar active
    p_existing == z_existing,

    p == p_new + p_existing,
    z == z_new + z_existing
]

## Cost Breakdown ##
c_battery_array= np.array([c_battery]*Ni)
c_charger_array= np.array([c_charger]*Ni)
c_subsidy_charger_array = np.array([c_subsidy_charger]*Ni)
c_solar_array = np.array([k_solar]*Ni) 
c_subsidy_solar_array = np.array([c_subsidy_solar]*Ni)

install_chg_cost = c_charger_array @ N_charger
solar_cost = c_solar_array @ Solar_Max

# Define per-node energy cost variables
energy_cost_new = cp.Variable(Ni)
energy_cost_existing = cp.Variable(Ni)

# Sum curtailed energy per node
curt_node = cp.sum(cp.reshape(curt, (Ni, Nt)), axis=1)  # shape (Ni,)

# Parameter for super charge cost
super_charge_param = cp.Parameter((Ni, Nt), value = super_charge_array)

if location == "Savannah":
    demand_cost = demand_charge * cp.max(cp.reshape(z_new,(Ni, Nt)), axis=1) # per node
    fic_cost = cp.sum(cp.multiply(fic_charger_array, cp.reshape(curt, (Ni, Nt))), axis=1)
    energy_charge_cost = weighted_charge_array @ cp.reshape(z_new,(Ni, Nt)).T + flat_charge

    constraints += [
        energy_cost_new >= energy_charge_cost 
                            + demand_cost 
                            - fic_cost,  
        energy_cost_existing >= cp.sum(cp.multiply(super_charge_param, cp.reshape(z_existing, (Ni, Nt))), axis=1)
]  
    

elif location == "LongBeach": 
    # --- Reshape demand arrays for broadcasting ---
    base_demand_array_2d      = base_demand_array.reshape(1, Nt)       # shape (1, Nt)
    low_peak_demand_array_2d  = low_peak_demand_array.reshape(1, Nt)  # shape (1, Nt)
    high_peak_demand_array_2d = high_peak_demand_array.reshape(1, Nt) # shape (1, Nt)

    # --- Calculate peak demand per node ---
    base_peak_per_node     = cp.max(cp.multiply(base_demand_array_2d, cp.reshape(z_new,(Ni, Nt))), axis=1)      # shape (Ni,)
    low_peak_per_node      = cp.max(cp.multiply(low_peak_demand_array_2d, cp.reshape(z_new,(Ni, Nt))), axis=1)  # shape (Ni,)
    high_peak_per_node     = cp.max(cp.multiply(high_peak_demand_array_2d, cp.reshape(z_new,(Ni, Nt))), axis=1) # shape (Ni,)

    # --- Demand cost per node ---
    demand_cost = cp.maximum(base_peak_per_node,
                                  cp.maximum(low_peak_per_node, high_peak_per_node))

    # --- Energy charge (weighted rates) ---
    total_rate = base_array + low_peak_array + high_peak_array  # shape (Nt,)
    total_rate_2d = total_rate.reshape(1, Nt)                   # shape (1, Nt)
    energy_charge_cost = cp.reshape(total_rate_2d @cp.reshape(z_new, (Nt, Ni)), (Ni,)) + flat_charge  # shape (Ni,)

    # --- Feed-in credit ---
    fic_cost = cp.sum(cp.multiply(fic_charger_array, cp.reshape(curt, (Ni, Nt))), axis=1)  # per node, shape (Ni,)

    # --- Constraints ---
    constraints += [
        energy_cost_new >= energy_charge_cost + demand_cost - fic_cost,  # per node
        energy_cost_existing >= cp.sum(cp.multiply(super_charge_param, cp.reshape(z_existing, (Ni, Nt))), axis=1)  # per node
    ]

elif location == "Houston":
    demand_cost = demand_charge * cp.max(cp.reshape(z_new, (Ni, Nt)), axis=1)  # per node
    fic_cost = cp.sum(cp.multiply(fic_charger_array, cp.reshape(curt, (Ni, Nt))), axis=1)  # per node
    energy_charge_cost = cp.reshape(z_new, (Ni, Nt)) @ energy_charge_array + flat_charge  # per node

    constraints += [
        energy_cost_new >= energy_charge_cost
                            - fic_cost
                            + demand_cost,
        energy_cost_existing >= cp.sum(cp.multiply(super_charge_param, cp.reshape(z_existing, (Ni, Nt))), axis=1)
    ]
else:
    raise ValueError("Error: location not recognized.")

# Bound energy cost per node based on install_binary
'''constraints += [
energy_cost_new <= M * install_binary,
energy_cost_existing <= M * (1 - install_binary),
]'''

# Total energy cost
print("super_charge_array sum:", np.sum(super_charge_array))


# Subsidies in Place affect on objective if part of it
# Subsidy
subsidies_benefit = 0
carbon_cost = 0
if subsidy_types["truck"]:
    subsidies_benefit += c_subsidy_truck * Ytotal_elec
if subsidy_types["charger"]:
    subsidies_benefit += c_subsidy_charger_array @ N_charger
if subsidy_types["solar"]:
    subsidies_benefit += c_subsidy_solar_array @ Solar_Max
if subsidy_types["carbon"]:
    carbon_cost += carbon_price * (intensity_elec * (cp.sum(z_existing) + cp.sum(z_new)) + intensity_gas * ((Gij*theta_gas)@y_gas + (Gij*(1 - theta_gas))@(L_gas/truck_load_gas)))

'''
Version uses a cost penalty for non-renewable power
'''
def solve(power_penalty, return_all=False):
    # reg = 0.3 # $/KWh
    # print("reg", reg)
    # c_carb = np.array([power_penalty/1000]*Nt) # penatly for non-renewable power $/kWh
    # c_power = np.array([reg]*Nt)
    
    # print("p_new shape:", p_new.shape)
    # print("CopyTime shape:", CopyTime.shape)
    # print("install_binary shape:", install_binary.shape)
    # print("CopyTime @ install_binary shape:", (CopyTime @ install_binary).shape)
    # print("Solar Max", Solar_Max.shape)

    prob = cp.Problem(cp.Minimize(c_space@W + c_truck_elec*Ytotal_elec + 
                                  c_truck_gas*Ytotal_gas +
                                  price_per_gallon * ((Gij*theta_gas)@y_gas + 
                                                      (Gij*(1 - theta_gas))@(L_gas/truck_load_gas))+  
                                  solar_cost + driver_cost_total + maintenance_cost_total + 
                                  cp.sum(energy_cost_new) + 
                                  cp.sum(energy_cost_existing) + 
                                  install_chg_cost + c_battery_array@B + carbon_cost - 
                                  subsidies_benefit + c_unmet*cp.sum(q)),constraints)  ### + c_carb@z + c_power@z + c_maint_elec*Ytotal_elec + c_maint_gas*Ytotal_gas (if maintenance annual)
    
    prob.solve(solver=cp.GUROBI) #, verbose= True) #reoptimize=True) 
    print("Problem status:", prob.status)

    return [sum(p.value)/1000, Ytotal_elec.value, Ytotal_gas.value, N_charger, sum(W.value), 
            sum(z.value)/1000, sum(p.value),sum(p.value) - sum(z.value), Solar_Max]  # sum(z.value)/1000   sum(p.value) - sum(z.value

'''
Solving the functions
'''
t0 = time.time()
power_penalty = 50 # $/ton
[non_renewables, truckFleetElectric, truckFleetGas, numberOfChargers, storageSize, non_renewables_used, pUsed, renewablesUsed, Solar_Max] = solve(power_penalty*0.389) # 50*0.389
print("non_renewables, truckFleetElectric, truckFleetGas, numberOfChargers, storageSize, non_renewables_used, pUsed, renewablesUsed, solarInstalled", [non_renewables, truckFleetElectric, truckFleetGas, numberOfChargers, storageSize, non_renewables_used, pUsed, renewablesUsed, Solar_Max] )

t1 = time.time()
print('Time taken: ',end='')
timeTaken = t1-t0
print(timeTaken)

### PRINT OBJECTIVE BREAKDOWN ###
print("========= Cost Breakdown =========")
print(f"Total Space Cost             : ${c_space @ W.value:,.2f}")
print(f"Total Electric Truck Cost     : ${c_truck_elec * Ytotal_elec.value:,.2f}")
print(f"Total Gas Truck Cost          : ${c_truck_gas * Ytotal_gas.value:,.2f}")
print(f"Total Fuel Cost              : ${price_per_gallon * ((Gij*theta_gas)@y_gas.value + (Gij*(1 - theta_gas))@(L_gas.value/truck_load_gas)):,.2f}")
print(f"Total Energy Cost            : ${sum(energy_cost_new.value) + sum(energy_cost_existing.value):,.2f}")
print(f"  - Energy Cost from New Power : ${sum(energy_cost_new.value):,.2f}")
print(f"  - Energy Cost from Existing Power : ${sum(energy_cost_existing.value):,.2f}")
print(f"Total Driver Cost            : ${driver_cost_total.value:,.2f}")
print(f"  - Driver Cost for Electric Trucks : ${electric_driver_cost.value:,.2f}")
print(f"  - Driver Cost for Gas Trucks : ${gas_driver_cost.value:,.2f}")
print(f"Total Maintenance Cost       : ${maintenance_cost_total.value:,.2f}")
print(f"  - Maintenance Cost for Electric Trucks : ${electric_maintenance_cost.value:,.2f}")
print(f"  - Maintenance Cost for Gas Trucks : ${gas_maintenance_cost.value:,.2f}")
print(f"Total Charger Installation Cost : ${install_chg_cost.value:,.2f}")
print(f"Total Solar Installation Cost : ${solar_cost.value:,.2f}")
print(f"Total Battery Cost           : ${c_battery_array @ B.value:,.2f}")

'''
# Subsidies print which types are active
truck = 'truck' if subsidy_types['truck'] else ''
charger = 'charger' if subsidy_types['charger'] else ''
solar = 'solar' if subsidy_types['solar'] else ''
carbon = 'carbon' if subsidy_types['carbon'] else ''

print(
    f"Total Subsidies Benefit from {truck} {charger} {solar} {carbon} subsidies: "
    f"${subsidies_benefit.value:,.2f}"
),.2f}")
'''
print(f"Total Unmet Demand Cost      : ${c_unmet * sum(q.value):,.2f}") 

# Portion of Cost for Electric vs Gas Trucks  
electric_truck_cost_pct = (c_truck_elec * Ytotal_elec.value) + electric_driver_cost.value + electric_maintenance_cost.value
gas_truck_cost_pct = (c_truck_gas * Ytotal_gas.value) + gas_driver_cost.value + gas_maintenance_cost.value   


# ============================ Summary Outputs ============================
print("========= Fleet Overview =========")
print(f"Total Electric Fleet Size     : {Ytotal_elec.value:.2f}")
print(f"Total Gas Fleet Size          : {Ytotal_gas.value:.2f}")
print(f"Total Electric Load           : {sum(L_elec.value):,.2f}")
print(f"Total Gas Load                : {sum(L_gas.value):,.2f}")
print(f"Total Unmet Products          : {sum(q.value):,.2f}")
print(f"Total Number of Chargers      : {sum(N_charger.value):.2f}")
print(f"Total Outflow (Curtailment)   : {np.sum(curt_mat.value):,.2f}")
print(f"Total Solar Installed         : {sum(Solar_Max.value)}")
print(f"Total Number of Batteries     : {sum(B.value):.2f}")
print(f"Total Power (Sum of p)        : {sum(p.value):,.2f}")

# ============================ Total Operation Cost ============================
total_operation_cost = (c_truck_elec * Ytotal_elec.value) + (c_truck_gas * Ytotal_gas.value) + \
                       price_per_gallon * ((Gij*theta_gas)@y_gas.value + (Gij*(1 - theta_gas))@(L_gas.value/truck_load_gas)) + \
                       sum(energy_cost_new.value) + sum(energy_cost_existing.value) + \
                       driver_cost_total.value + maintenance_cost_total.value + install_chg_cost.value + solar_cost.value
print(f"Total Operational Cost        : ${total_operation_cost:,.2f}")

# ============================== Total Capital Cost ============================
total_capital_cost = c_space @ W.value  + c_truck_elec * Ytotal_elec.value + c_truck_gas * Ytotal_gas.value + install_chg_cost.value + solar_cost.value + c_battery_array @ B.value 
# ============================ Distance Calculation ============================
total_dist_electric = sum(
    y_elec[j + t * Nj * 2].value * transport_links[j][2]
    for j in range(Nj) for t in range(Nt)
)
print(f"Total Distance Traveled for Electric: {total_dist_electric:,.2f} miles")

total_dist_gas= sum(
    y_gas[j + t * Nj * 2].value * transport_links[j][2]
    for j in range(Nj) for t in range(Nt)
)
print(f"Total Distance Traveled for Gas: {total_dist_gas:,.2f} miles")

total_dist = sum(
    (y_elec[j + t * Nj * 2].value + y_gas[j + t * Nj * 2].value) * transport_links[j][2]
    for j in range(Nj) for t in range(Nt)
)
print(f"Total Distance Traveled       : {total_dist:,.2f} miles")


# ============================ Warehouses & Demand ============================
print(f"Total Warehouse Capacity      : {sum(W.value):,.2f}")
print(f"Total Demand                  : {total_demand:,.2f}")

# ============================ Power Breakdown ============================
total_power = np.sum([p[i + t * Ni].value for t in range(Nt) for i in range(Ni)])
total_nonrenewables = np.sum([z[i + t *Ni].value for t in range(Nt) for i in range(Ni)])
total_renewables_used = total_power - total_nonrenewables

print("========= Power Overview =========")
print(f"Total Power Used              : {total_power:,.2f}")
print(f"Total Nonrenewables Used      : {total_nonrenewables:,.2f}")
print(f"Total Renewables Used         : {total_renewables_used:,.2f}")
print(f"Total Solar Generation        : {np.sum(solar_generation.value)}")
# ============================ Cost Calculations ============================
Total_Electric_Driver_cost = cp.sum([
    c_driver * transport_links[j][2] * y_elec[j + t * Nj * 2].value
    for j in range(Nj) for t in range(Nt)
])
Total_Gas_Driver_cost = cp.sum([
    c_driver * transport_links[j][2] * y_gas[j + t * Nj * 2].value
    for j in range(Nj) for t in range(Nt)
])
Total_Driver_cost = Total_Electric_Driver_cost + Total_Gas_Driver_cost

Total_Elec_Maint = cp.sum([
    c_maintenance_elec * transport_links[j][2] * y_elec[j + t * Nj * 2].value
    for j in range(Nj) for t in range(Nt)
])
Total_Gas_Maint = cp.sum([
    c_maintenance_gas * transport_links[j][2] * y_gas[j + t * Nj * 2].value
    for j in range(Nj) for t in range(Nt)
])
Total_Maintenance_cost = Total_Elec_Maint + Total_Gas_Maint

Solar_Cost = c_solar_array @ Solar_Max.value
Charger_Cost = c_charger_array @ N_charger.value

Warehouse_Cost = c_space @ W.value
Electric_Truck_Cost = c_truck_elec * Ytotal_elec.value
Gas_Truck_Cost = c_truck_gas * Ytotal_gas.value

## Calculating energy used by trucks 
# Get values after solving
y_elec_val = y_elec.value        # trucks moving (shape aligned with transport links/time)
L_elec_val = L_elec.value        # load on trucks

# Calculate energy for empty truck trips
empty_truck_energy = Depart @ (np.diag(Eij * theta_elec) @ y_elec_val)

# Calculate additional energy for the load
load_energy = Depart @ (np.diag(Eij * (1 - theta_elec)) @ (L_elec_val / truck_load_elec))

# Total energy consumed by trucks
total_energy_consumed = np.sum(empty_truck_energy + load_energy)
print(f"Total Energy Consumed by Trucks (kWh): {total_energy_consumed}")

# ============================ Utility Bills ============================
if location == "Savannah":
    Energy_Charge = sum(energy_charge_cost.value)
    #print("Energy_Charge", Energy_Charge)
    Demand_Charge = sum(demand_cost.value)
    #print("Demand_Charge", Demand_Charge)
    FIC_Charge = -1 * (sum(fic_cost.value))
    #print( "FIC_Charge", FIC_Charge)

    Energy_New_Cost = sum(energy_cost_new.value)
    #print("Energy_New_Cost", Energy_New_Cost)

    Energy_Existing_Cost = sum(energy_cost_existing.value)
    #print( "Energy_Existing_Cost", Energy_Existing_Cost)
    Energy_Bill = Energy_New_Cost + Energy_Existing_Cost

    #print("Bill", Energy_Bill)
    #print("Bill2", sum(Energy_Charge + Demand_Charge + FIC_Charge))

elif location == "LongBeach":
    Energy_Charge = sum(energy_charge_cost.value)
    Demand_Charge = sum(demand_cost.value)
    FIC_Charge = sum(fic_cost.value)  # positive if credit
    Energy_New_Cost = sum(energy_cost_new.value)  # includes energy + demand - FIC
    Energy_Existing_Cost = sum(energy_cost_existing.value)
    Energy_Bill = Energy_New_Cost + Energy_Existing_Cost


elif location == "Houston":
    Energy_Charge = sum(energy_charge_cost.value)
    Demand_Charge = sum(demand_cost.value)
    FIC_Charge = -1 * (sum(fic_cost.value))
    Energy_New_Cost = sum(energy_cost_new.value)
    Energy_Existing_Cost = sum(energy_cost_existing.value)
    Energy_Bill = Energy_New_Cost + Energy_Existing_Cost

else:
    print("Error: location not recognized.")

# print("Energy Bill", Energy_Bill) 


Gas_Bill = price_per_gallon * (
    (Gij * theta_gas) @ y_gas.value + (Gij * (1 - theta_gas)) @ (L_gas.value / truck_load_gas)
)

print("========= Cost Summary =========")
print(f"Total Driver Cost             : {Total_Driver_cost:,.2f}")
print(f"Total Maintenance Cost        : {Total_Maintenance_cost:,.2f}")
print(f"Total Solar Capacity Installed: {sum(Solar_Max.value):,.2f}")
print(f"Total Charger Cost            : {Charger_Cost:,.2f}")
print(f"Total Battery Cost            : {c_battery_array@B.value:,.2f}")
print(f"Total Solar Cost              : {Solar_Cost:,.2f}")
print(f"Total Warehouse Cost          : {Warehouse_Cost:,.2f}")
print(f"Total Electric Truck Cost     : {Electric_Truck_Cost:,.2f}")
print(f"Total Gas Truck Cost          : {Gas_Truck_Cost:,.2f}")
print("--------- Utility Bills ---------")
print(f"Total Energy Bill             : {Energy_Bill:,.2f}")
print(f"  └─ Energy Charge            : {Energy_Charge:,.2f}")
print(f"  └─ Demand Charge            : {Demand_Charge:,.2f}")
print(f"  └─ Feed-In Credit (FIC)     : {FIC_Charge:,.2f}")
print(f"  └─ Exisitng Infrastructure Chg : {Energy_Existing_Cost:,.2f}")
print(f"Total Gas Bill                : {Gas_Bill:,.2f}")

# ============================ CSV Output ============================
parameters = {
    "Buy Back Period": buyback_period_truck_elec,
    "truck_load_elec": truck_load_elec,
    "truck_load_gas": truck_load_gas,
    "truck_weight_elec": truck_weight_elec,
    "truck_weight_gas": truck_weight_gas,
    "kWh_per_mile": kWh_per_mile,
    "gallon_per_mile": gallon_per_mile,
    "price_per_gallon": price_per_gallon,
    "Cost Maintenance Elec": c_maintenance_elec,
    "Cost Maintenance Gas": c_maintenance_gas,
    "Subsidy Truck": c_subsidy_truck,
    "Subsidy Charger": c_subsidy_charger,
    "Charging_Speed": charging_speed,
    "Cost of Solar": cost_solar
}

results = {
    "Total Electric Fleet Size": Ytotal_elec.value,
    "Total Gas Fleet Size": Ytotal_gas.value,
    "Total Electric Load": sum(L_elec.value),
    "Total Gas Load": sum(L_gas.value),
    "Total Unmet Demand": sum(q.value),
    "Total Number of Chargers": sum(N_charger.value),
    "Total Number of Batteries" : sum(B.value),
    "Total Power (sum p)": sum(p.value),
    "Total Outflow (sum curt)": sum(curt_mat.value),
    "Total Warehouse (kg)": sum(W.value),
    "Total Distance Electric (miles)": total_dist_electric,
    "Total Distance Gas (miles)": total_dist_gas,
    "Total Distance (miles)": total_dist,
    "Total Nonrenewables": total_nonrenewables,
    "Total Renewables Used": total_renewables_used,
    "Total Driver Cost": Total_Driver_cost,
    "Total Maintenance Cost": Total_Maintenance_cost,
    "Total Solar Capacity": sum(Solar_Max.value)
}

# Combine parameters and results
all_data = {**parameters, **results}

# Save to CSV
filename = location + "_results/results_summary.csv"
file_exists = os.path.isfile(filename)

with open(filename, 'a', newline='') as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=all_data.keys())
    if not file_exists:
        writer.writeheader()
    writer.writerow(all_data)

print(f"✅ Results successfully appended to: {filename}")

#### SAVE TO RESULTS ####

# ---- Construct File Path ----
csv_path = (
    location
    + "_results/summary/results_summary_" + network_type + "_" + demand_network_type + "_" + manufacturer + "_solar_subsidies_" + str(subsidy_types["solar"])  + "_truck_subsidies_" + str(subsidy_types["truck"]) + "_charger_subsidies_" + str(subsidy_types["charger"]) +  "_truck_weight_" + str(subsidy_types["weight_truck"]) +  "carbon" + str(subsidy_types["carbon"]) +  "_" + str(Nt)
    + ".csv"
)

# ---- Load Existing DataFrame or Initialize New One ----
if os.path.exists(csv_path):
    df = pd.read_csv(csv_path, index_col=0)
else:
    df = pd.DataFrame()

# ---- Build Result Dictionary ----
results = {
    "Total Electric Fleet Size": Ytotal_elec.value,
    "Total Gas Fleet Size": Ytotal_gas.value,
    "Total Electric Load": sum(L_elec.value),
    "Total Gas Load": sum(L_gas.value),
    "Total Unmet Demand": sum(q.value),
    "Total Number of Chargers": sum(N_charger.value),
    "Total Number of Batteries": sum(B.value),
    "Total Power (sum p)": sum(p.value),
    "Total Outflow (sum curt)": sum(curt.value),
    "Total Warehouse (kg)": sum(W.value),
    "Total Distance Electric (miles)": total_dist_electric,
    "Total Distance Gas (miles)": total_dist_gas,
    "Total Distance (miles)": total_dist,
    "Total Nonrenewables": total_nonrenewables,
    "Total Renewables Used": total_renewables_used,
    "Total Driver Cost": Total_Driver_cost,
    "Total Maintenance Cost": Total_Maintenance_cost,
    "Total Solar Capacity": sum(Solar_Max.value),
    "Total Charger Cost": Charger_Cost,
    "Total Battery Cost": float(c_battery_array @ B.value),
    "Total Solar Cost": Solar_Cost,
    "Total Warehouse Cost": Warehouse_Cost,
    "Total Electric Truck Cost": Electric_Truck_Cost,
    "Total Gas Truck Cost": Gas_Truck_Cost,
    "Total Energy Bill": Energy_Bill,
    "Energy Charge": Energy_Charge,
    "Demand Charge": Demand_Charge,
    "Feed-In Credit (FIC)": FIC_Charge,
    "Existing Infrastructure Chg": Energy_Existing_Cost,
    "Total Gas Bill": Gas_Bill,
    "Time Taken": timeTaken
}

'''
# ---- Add or Overwrite Column ----
df[str(buy_back_time)] = pd.Series(results, name=str(buy_back_time))

# ---- Sort Columns by Numeric Buyback Value ----
df = df[sorted(df.columns, key=lambda x: float(x))]

os.makedirs(os.path.dirname(csv_path), exist_ok=True)
# ---- Save Updated Results ----
df.to_csv(csv_path)
print(f"✅ Saved (or updated) buyback {buy_back_time} to CSV.")
'''
# ---- Clean and Convert Result Dictionary ----
column_key = str(float(buy_back_time))  # Ensure numeric column sort

clean_results = {}
for k, v in results.items():
    clean_results[k] = float(np.sum(v)) if isinstance(v, (np.ndarray, list)) else float(v)


# ---- Add or Overwrite Column ----
df[column_key] = pd.Series(clean_results, name=column_key)

# ---- Sort Columns by Numeric Buyback Value ----
df = df[sorted(df.columns, key=lambda x: float(x))]

# ---- Create Directory and Save ----
output_dir = os.path.dirname(csv_path)
os.makedirs(output_dir, exist_ok=True)

# ---- Save Updated Results ----
df.to_csv(csv_path)
print(f"✅ Successfully saved buyback {buy_back_time} to {csv_path}")


# ============================ HISTOGRAM FOR COSTS ============================
costs = {
    "Electric Driver Cost": Total_Electric_Driver_cost,
    "Gas Driver Cost": Total_Gas_Driver_cost,
    "Electric Maintenance": Total_Elec_Maint,
    "Gas Maintenance": Total_Gas_Maint,
    "Electric Truck Cost": Electric_Truck_Cost,
    "Gas Truck Cost": Gas_Truck_Cost,
    "Charger Cost": Charger_Cost,
    "Solar Cost": Solar_Cost,
    "Warehouse Cost": Warehouse_Cost,
    "Energy Bill": Energy_Bill,
    "Gas Bill": Gas_Bill
}

# Prepare labels and values
labels = list(costs.keys())
values = list(costs.values())

# Define colors: electric (blue), gas (orange), shared (gray)
colors = []
for label in labels:
    if "Electric" in label or "Charger" in label or "Solar" in label:
        colors.append("#1f77b4")  # blue for electric
    elif "Gas" in label:
        colors.append("#ff7f0e")  # orange for gas
    else:
        colors.append("#7f7f7f")  # gray for shared components

# Create the bar chart
plt.figure(figsize=(12, 6))
bars = plt.bar(labels, values, color=colors)
plt.xticks(rotation=45, ha='right')
plt.ylabel("Cost ($)")
plt.title("Cost Breakdown by Type (Electric vs. Gas)")
plt.tight_layout()

# Optional: annotate values on top of each bar
for bar in bars:
    height = bar.get_height()
    plt.annotate(f'${height:,.0f}', xy=(bar.get_x() + bar.get_width() / 2, height),
                 xytext=(0, 3), textcoords="offset points", ha='center', va='bottom')

# ============================ SAVE PLOT ============================
# Ensure folder exists
output_folder = location + "_plots"
os.makedirs(output_folder, exist_ok=True)

# Build filename
filename = (
    f"histogram_{location}_{network_type}_{demand_network_type}_{manufacturer}_{buy_back_time}"
    f"_solar_subsidies_{subsidy_types['solar']}"
    f"_truck_subsidies_{subsidy_types['truck']}"
    f"_charger_subsidies_{subsidy_types['charger']}"
    f"_truck_weight_{subsidy_types['weight_truck']}"
    f"_carbon_{subsidy_types['carbon']}"
    f"_Nt_{Nt}.png"
)
filepath = os.path.join(output_folder, filename)

plt.savefig(filepath)
plt.close()

print(f"Plot saved to: {filepath}")


'''
Prints outputs to a log
'''        
# one row for each time
headers = ['Time Step'] + ['Charge '+i for i in nodes]
headers += ['Electric Trucks '+i for i in nodes]
headers += ['Gas Trucks '+i for i in nodes]
headers += ['Goods '+i for i in nodes]
headers += ['Unmet Demand '+i for i in nodes]
headers += ['Power '+i for i in nodes]
headers += ['Power (New Chargers) '+i for i in nodes]
headers += ['Power (Existing) '+i for i in nodes]
headers += ['Non-Renewables '+i for i in nodes]
headers += ['Non-Renewables (New Chargers) '+i for i in nodes]
headers += ['Non-Renewables (Existing) '+i for i in nodes]
headers += ['Number of Chargers' + i for i in nodes]
headers += ['Departed (Electric)'+iNodes[r[0]]+ ' for ' + iNodes[r[1]] for r in transport_links]
headers += ['Departed (Electric)'+iNodes[r[1]]+ ' for ' + iNodes[r[0]] for r in transport_links]
headers += ['Departed (Gas)'+iNodes[r[0]]+ ' for ' + iNodes[r[1]] for r in transport_links]
headers += ['Departed (Gas)'+iNodes[r[1]]+ ' for ' + iNodes[r[0]] for r in transport_links]
headers += ['Departed Load (Electric)'+iNodes[r[0]]+ ' for ' + iNodes[r[1]] for r in transport_links]
headers += ['Departed Load (Electric)'+iNodes[r[1]]+ ' for ' + iNodes[r[0]] for r in transport_links]
headers += ['Departed Load (Gas)'+iNodes[r[0]]+ ' for ' + iNodes[r[1]] for r in transport_links]
headers += ['Departed Load (Gas)'+iNodes[r[1]]+ ' for ' + iNodes[r[0]] for r in transport_links]


file_path = location + '_results/results' + '_' + product_type + '_' + manufacturer + '_' + network_type + '_' + demand_network_type  + "_solar_subsidies_" + str(subsidy_types["solar"])  + "_truck_subsidies_" + str(subsidy_types["truck"]) + "_truck_weight_" + str(subsidy_types["weight_truck"]) + "_charger_subsidies_" + str(subsidy_types["charger"]) + "_carbon" + str(subsidy_types["carbon"])  + '_buyback_' + str(float(buy_back_time)) + '_Nt_' + str(int(Nt)) + '.csv'

with open(file_path,'w') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(headers)
    for t in range(Nt):
        row = [t]
        for i in range(Ni):
            row += [C[i+t*Ni].value]
        for i in range(Ni):
            row += [Y_elec[i+t*Ni].value]
        for i in range(Ni):
            row += [Y_gas[i+t*Ni].value]
        for i in range(Ni):
            row += [x[i+t*Ni].value]
        for i in range(Ni):
            row += [q[i+t*Ni].value]
        for i in range(Ni): row += [p_mat[i, t].value]
        for i in range(Ni): row += [p_new_mat[i, t].value]
        for i in range(Ni): row += [p_existing_mat[i, t].value]
        for i in range(Ni): row += [z_mat[i, t].value]
        for i in range(Ni): row += [z_new_mat[i, t].value]
        for i in range(Ni): row += [z_existing_mat[i, t].value]
        for i in range(Ni):
            row += [N_charger[i].value]
        for j in range(Nj):
            row += [y_elec[j+t*Nj*2].value]
        for j in range(Nj):
            row += [y_elec[j+Nj+t*Nj*2].value]
        for j in range(Nj):
            row += [y_gas[j+t*Nj*2].value]
        for j in range(Nj):
            row += [y_gas[j+Nj+t*Nj*2].value]
        for j in range(Nj):
            row += [L_elec[j+t*Nj*2].value]
        for j in range(Nj):
            row += [L_elec[j+Nj+t*Nj*2].value]
        for j in range(Nj):
            row += [L_gas[j+t*Nj*2].value]
        for j in range(Nj):
            row += [L_gas[j+Nj+t*Nj*2].value]
        


        writer.writerow(row)

