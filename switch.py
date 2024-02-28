#!/usr/bin/python3
import sys
import struct
import wrapper
import threading
import time
from wrapper import recv_from_any_link, send_to_link, get_switch_mac, get_interface_name

def parse_ethernet_header(data):
    # Unpack the header fields from the byte array
    #dest_mac, src_mac, ethertype = struct.unpack('!6s6sH', data[:14])
    dest_mac = data[0:6]
    src_mac = data[6:12]
    
    # Extract ethertype. Under 802.1Q, this may be the bytes from the VLAN TAG
    ether_type = (data[12] << 8) + data[13]

    vlan_id = -1
    # Check for VLAN tag (0x8100 in network byte order is b'\x81\x00')
    if ether_type == 0x8200:
        vlan_tci = int.from_bytes(data[14:16], byteorder='big')
        vlan_id = vlan_tci & 0x0FFF  # extract the 12-bit VLAN ID
        ether_type = (data[16] << 8) + data[17]

    return dest_mac, src_mac, ether_type, vlan_id

def create_vlan_tag(vlan_id):
    # 0x8100 for the Ethertype for 802.1Q
    # vlan_id & 0x0FFF ensures that only the last 12 bits are used
    return struct.pack('!H', 0x8200) + struct.pack('!H', vlan_id & 0x0FFF)

def is_unicast(mac_address):
    first_byte = int(mac_address.split(':')[0], 16)
    return (first_byte & 1) == 0

def check_for_send(port_dest,data, length, interface, vlan_id, MAC_Table, SW_Table, Port_State):
    #valori auxiliare pe care le vom trimitem
    aux_data = data
    aux_length = length
    #daca avem un pachet fara tag, atunci verificam daca portul destinatie este de tip trunk si daca este DESIGNATED
    if vlan_id == -1:
        if SW_Table[get_interface_name(port_dest)] == "T" and Port_State[get_interface_name(port_dest)] == "DESIGNATED":
            #vom adauga tag-ul si il vom trimite
            aux_data = data[0:12] + create_vlan_tag(int(SW_Table[get_interface_name(interface)])) + data[12:]
            aux_length = length + 4
            send_to_link(port_dest, aux_data, aux_length)
        elif SW_Table[get_interface_name(port_dest)] != "T" and SW_Table[get_interface_name(port_dest)] == SW_Table[get_interface_name(interface)]:
            #in caz contrar doar trimitem
            send_to_link(port_dest, aux_data, aux_length)
    else:
        #in caz contrar scaotem tagul
        aux_data = data[0:12] + data[16:]
        aux_length = length - 4
        #daca portul destinatie este de tip trunk si este DESIGNATED, atunci adaugam tag-ul si trimitem
        if SW_Table[get_interface_name(port_dest)] == "T" and Port_State[get_interface_name(port_dest)] == "DESIGNATED":
            aux_data = aux_data[0:12] + create_vlan_tag(vlan_id) + aux_data[12:]
            aux_length = aux_length + 4
            send_to_link(port_dest, aux_data, aux_length)
        elif SW_Table[get_interface_name(port_dest)] != "T" and int(SW_Table[get_interface_name(port_dest)]) == vlan_id:
            #in caz contrar trimitem fara tag
            send_to_link(port_dest, aux_data, aux_length)

    

def send_bdpu_every_sec(own_bridge_id,interfaces, SW_Table,root_bridge_id, root_path_cost):
    global is_root
    while True:
        #daca este root, atunci trimitem la toate porturile de tip trunk
        if own_bridge_id == root_bridge_id:
            for i in interfaces:
                if SW_Table[get_interface_name(i)] == "T":
                    #construim structura pentru BDPU si o trimitem
                    BDPU_Mac = "01:80:C2:00:00:00"
                    aux = "de:ad:be:ef:11:11"
                    root_path_cost = 0
                    BDPU_struct = [int(byte, 16) for byte in BDPU_Mac.split(':')]
                    BDPU_struct = BDPU_struct + [int(byte, 16) for byte in aux.split(':')] + [00] + [38] +[66] + [66] + [3] + [0] + [0] + [0] + [0] + [own_bridge_id] + [own_bridge_id] + [root_path_cost]
                    BDPU_struct = bytes(BDPU_struct)
                    send_to_link(i, BDPU_struct, 58)
                    time.sleep(1)
                


                

        

def main():
    # init returns the max interface number. Our interfaces
    # are 0, 1, 2, ..., init_ret value + 1
    switch_id = sys.argv[1]

    num_interfaces = wrapper.init(sys.argv[2:])
    interfaces = range(0, num_interfaces)

    print("# Starting switch with id {}".format(switch_id), flush=True)
    print("[INFO] Switch MAC", ':'.join(f'{b:02x}' for b in get_switch_mac()))

    #initializare prioritate, root_port, Tabela cu numele Switch-urilor si statusul fiecui port(BLOCKED SAU DESIGNATED)

    Port_State = {}
    root_port = 0

    prio = -1
    SW_Table = {}

    #am deschis fisierul pentru a putea citi din el

    with open("configs/switch{}.cfg".format(switch_id)) as f:
        lines = f.readlines()
        for i,line in enumerate(lines):
            cleaned_line = line.strip()
            if i == 0:
                prio = int(cleaned_line)
            else:
                worlds = cleaned_line.split(" ")
                #adaugam in dictionar numele SWITCH-urilor si tipul lor (T , 1 sau 2 in cazul nostru)
                SW_Table[worlds[0]] = worlds[1]

    #initializare tabela MAC

    MAC_Table = {}

    #la inceput daca interfata este de tip trunk, atunci portul este BLOCKED

    for i in interfaces:
        if(SW_Table[get_interface_name(i)] == "T"):
            Port_State[get_interface_name(i)] = "BLOCKED"

    #ca in pseudocod, initializam valorile pentru bridge

    own_bridge_id = prio
    root_bridge_id = own_bridge_id
    root_path_cost = 0

    #daca sunt egale, atunci toate sunt DESIGNATED

    if own_bridge_id == root_bridge_id:
        for i in interfaces:
            Port_State[get_interface_name(i)] = "DESIGNATED"

    # Create and start a new thread that deals with sending BDPU
    t = threading.Thread(target=send_bdpu_every_sec, args=(own_bridge_id, interfaces, SW_Table, root_bridge_id, root_path_cost))
    t.start()

    # Printing interface names
    for i in interfaces:
        print(get_interface_name(i))

    while True:
        # Note that data is of type bytes([...]).
        # b1 = bytes([72, 101, 108, 108, 111])  # "Hello"
        # b2 = bytes([32, 87, 111, 114, 108, 100])  # " World"
        # b3 = b1[0:2] + b[3:4].
        interface, data, length = recv_from_any_link()

        aux = data[0:6]
        aux = ':'.join(f'{b:02x}' for b in aux)

        #cazul in care nu avem un pachet de tip BDPU

        if aux != "01:80:c2:00:00:00":
            dest_mac, src_mac, ethertype, vlan_id = parse_ethernet_header(data)

            # Print the MAC src and MAC dst in human readable format
            dest_mac = ':'.join(f'{b:02x}' for b in dest_mac)
            src_mac = ':'.join(f'{b:02x}' for b in src_mac)

            # Note. Adding a VLAN tag can be as easy as
            # tagged_frame = data[0:12] + create_vlan_tag(10) + data[12:]

            print(f'Destination MAC: {dest_mac}')
            print(f'Source MAC: {src_mac}')
            print(f'EtherType: {ethertype}')

            print("Received frame of size {} on interface {}".format(length, interface), flush=True)

            # TODO: Implement forwarding with learning

            #adaugam in tabela MAC adresa sursa si interfata de pe care am primit-o

            MAC_Table[src_mac] = interface

            #daca adresa destinatie este de tip unicast, atunci verificam daca se afla in tabela MAC
            if is_unicast(dest_mac):
                if dest_mac in MAC_Table:
                    #trimitem mai departe
                    check_for_send(MAC_Table[dest_mac],data, length, interface, vlan_id, MAC_Table, SW_Table, Port_State)
                else:
                    #in caz contrar trimitem la restul porturilor
                    for o in interfaces:
                        if o != interface:
                            check_for_send(o,data, length, interface, vlan_id, MAC_Table, SW_Table, Port_State)
            else:
                #in caz contrar trimitem la restul porturilor
                for o in interfaces:
                    if o != interface:
                        check_for_send(o,data, length, interface, vlan_id, MAC_Table, SW_Table, Port_State)
        else:
                
            #extragem ce avem nevoie din BDPU
            dest_mac = data[0:6]
            BDPU_root_bridge_id = data[21]
            BDPU_sender_bridge_id = data[22]
            BDPU_sender_path_cost = data[23]
            
            #daca BDPU-ul pe care l-am prmit este mai mic, atunci il consideram root
            if BDPU_root_bridge_id < root_bridge_id:
                root_bridge_id = BDPU_root_bridge_id
                root_path_cost = BDPU_sender_path_cost + 10
                root_port = interface

                #daca este root, atunci toate porturile de tip trunk sunt BLOCKED, in afara de root_port
                for i in interfaces:
                    if SW_Table[get_interface_name(i)] == "T" and i != root_port:
                        Port_State[get_interface_name(i)] = "BLOCKED"

                #daca root-ul este blocked, atunci il deblocam
                if Port_State[get_interface_name(root_port)] == "BLOCKED":
                    Port_State[get_interface_name(root_port)] = "DESIGNATED"

                #trimitem BDPU la toate porturile de tip trunk in afara de root_port
                for i in interfaces:
                    if i != root_port:
                        if SW_Table[get_interface_name(i)] == "T":
                            BDPU_Mac = "01:80:C2:00:00:00"
                            BDPU_struct = [int(byte, 16) for byte in BDPU_Mac.split(':')]
                            aux = "de:ad:be:ef:11:11"
                            BDPU_struct = BDPU_struct + [int(byte, 16) for byte in aux.split(':')] + [00] + [38] +[66] + [66] + [3] + [0] + [0] + [0] + [0] + [own_bridge_id] + [own_bridge_id] + [root_path_cost]
                            BDPU_struct = bytes(BDPU_struct)
                            send_to_link(i, BDPU_struct, 58)

            #in acest caz vom creste costul numai daca BDPU-ul pe care l-am primit este mai mic decat cel pe care il avem
            elif BDPU_root_bridge_id == root_bridge_id:
                if interface == root_port and BDPU_sender_path_cost + 10 < root_path_cost:
                        root_path_cost = BDPU_sender_path_cost + 10
                #verificam daca portul ar trebui trecut pe DESIGNATED
                elif interface != root_port:
                    if BDPU_sender_path_cost > root_path_cost:
                        if Port_State[get_interface_name(interface)] != "DESIGNATED":
                            Port_State[get_interface_name(interface)] = "DESIGNATED"
            
            elif BDPU_sender_bridge_id == own_bridge_id:
                Port_State[get_interface_name(interface)] = "BLOCKED"

            #la final, daca suntem root, atunci toate porturile sunt DESIGNATED
            if own_bridge_id == root_bridge_id:
                for i in interfaces:
                    if i != root_port:
                        Port_State[get_interface_name(i)] = "DESIGNATED"

        # TODO: Implement VLAN support


        
        # TODO: Implement STP support

        # data is of type bytes.
        # send_to_link(i, data, length)

if __name__ == "__main__":
    main()
