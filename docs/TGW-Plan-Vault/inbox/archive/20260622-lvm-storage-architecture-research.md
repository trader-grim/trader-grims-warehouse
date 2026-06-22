Passing a database a raw Logical Volume Manager (LVM) block device skips the filesystem layer (e.g., ext4, XFS) to avoid I/O overhead and lock contention. The database manages the raw disk space directly. This requires exact disk preparation and configuration to prevent data loss.1. Create the LVM Logical VolumeFirst, create an unformatted Logical Volume (LV) to serve as your raw device. A Logical Volume is inherently a raw block device until you place a filesystem on it.Create the LV by specifying your volume group (e.g., vg_data), the desired name, and capacity:bashsudo lvcreate -n lv_database -L 500G vg_data
Use code with caution.Once created, your unformatted block device will be accessible at /dev/vg_data/lv_database.2. Configure Database PermissionsDatabases (like Oracle, PostgreSQL, or MongoDB) run under specific service accounts (e.g., oracle or postgres). Since they perform direct O_DIRECT disk writes, the user running the database must have read/write permissions for the block device.Change the ownership of the device to your database user and group:bashsudo chown postgres:postgres /dev/vg_data/lv_database
Use code with caution.Restrict permissions so only the database user can access it:bashsudo chmod 600 /dev/vg_data/lv_database
Use code with caution.3. Point the Database to the Raw BlockConfigure your database to utilize the raw device.Oracle: Use the Database Configuration Assistant (DBCA) and select Raw Devices for storage. You will map the datafiles to the character device path (often found in /dev/vg_data/r_lv_database).MongoDB: In your configuration file (mongod.conf), you can point storage paths directly to the block device.PostgreSQL: While PostgreSQL is typically bound to a mounted directory, you can utilize symlinks or tablespaces mapped to the raw block device when the database starts.Best Practices & ConsiderationsAvoid Formatting: Do not run mkfs.ext4 or mkfs.xfs on this Logical Volume. Formatting creates metadata overhead that interferes with the database's proprietary storage algorithms.Write Barriers: Raw block configurations are highly dependent on disk flushing (caching). Ensure your physical controllers and underlying arrays honor cache flushes (or use battery-backed caches) to guarantee durability on power loss.Resizing Restrictions: Because there is no filesystem, extending the LV requires database-level awareness and manipulation of block extents.If you can share which specific database (e.g., Oracle, MongoDB) you are attempting to configure, I can help outline the exact configuration files or SQL commands needed to map the storage.11 sitesLogical volume to raw device - AIX - Unix Linux Communityrlv_name1, rlv_name2 ... As i see, these LVs were turned into raw devices, somehow. I found out the way to create new logical volu...Unix Linux CommunityRaw Block Volume | OpenEBS DocsSome specialized applications require direct access to a block device because, for example, the file system layer introduces unnee...OpenEBSHow to create a Oracle Database on LVMNOTE: This step is optional. One can always choose the file while install. By setting this variable, DBCA will automatically popul...WordPress.comShow allYou said: postgresqlQuick results from the web:PostgreSQL: The world's most advanced open source databasePostgreSQL is a powerful, open source object-relational database system with over 35 years of active development that has earned it a strong reputation for ...PostgreSQL·https://www.postgresql.orgPostgreSQL - WikipediaPostgres is a free and open-source relational database management system (RDBMS) emphasizing extensibility and SQL compliance.Wikipedia·https://en.wikipedia.orgPostgreSQL cannot natively use a raw LVM block device directly as a tablespace or data directory. Unlike Oracle or MySQL (InnoDB), PostgreSQL requires an underlying filesystem (like XFS or ext4) to handle basic operating system calls like open(), fsync(), and directory structuring.To achieve the near-raw performance, low latency, and management benefits of LVM with PostgreSQL, you must format the LVM block with a highly optimized filesystem layer or pass the raw block to a virtualized environment.1. The Recommended Approach: Optimized Filesystem on LVMTo get maximum performance while fulfilling PostgreSQL's technical requirements, deploy the LVM volume using XFS with specific mount options that mimic raw device performance by disabling unnecessary metadata logging.Step 1: Create the LVM volumebashsudo lvcreate -n lv_postgres_data -L 500G vg_data
Use code with caution.Step 2: Format with XFS (Optimized for large block databases)bashsudo mkfs.xfs -f /dev/vg_data/lv_postgres_data
Use code with caution.Step 3: Mount with performance flagsMount using noatime (stops tracking file read times) and nodiratime via your /etc/fstab:text/dev/vg_data/lv_postgres_data  /var/lib/postgresql/data  xfs  defaults,noatime,nodiratime,allocsize=64m  0  2
Use code with caution.Step 4: Align PostgreSQL parametersSet wal_sync_method = fdatasync in your postgresql.conf to match XFS behavior.2. The Advanced Bypass: Virtual Machine Raw Device Mapping (RDM)If you are running PostgreSQL inside a virtual machine (VMware ESXi, KVM, or Hyper-V) and your host manages the LVM, you can pass the LVM block directly into the guest OS as a raw disk.Host Action: Map /dev/vg_data/lv_postgres_data directly to the VM instance as a raw SCSI block device.Guest Action: The PostgreSQL VM will see this as a local raw unformatted disk (e.g., /dev/sdb).Final Requirement: You will still need to format /dev/sdb with XFS inside the guest OS for PostgreSQL to use it.Comparison: Raw Device vs. Optimized LVM+XFSFeatureRaw Block Device (Unsupported)Optimized LVM + XFS (Recommended)Performance Overhead0% (Direct disk access)< 1% (Negligible with noatime)Point-in-Time RecoveryManual block trackingSupported via pg_waldump & LVM snapshotsOnline ResizingExtremely complexSeamless via lvextend + xfs_growfsCrash SafetyRelies entirely on DB engineDouble-protected via XFS journaling & Postgres WALWould you like help choosing the best XFS block size alignment based on your underlying storage array (like RAID 10 or NVMe), or do you need assistance migrating an existing cluster onto an LVM setup?7 siteslinux - Filesystem tuning for PostgreSQLJan 4, 2016 — Filesystem tuning for PostgreSQL [A] an ext4 for the root system ("/") with OS recommended/default settings. [B] an ext4 for postg...Database Administrators Stack ExchangePostgreSQL Create Database Options and SettingsFeb 26, 2024 — In fact, PostgreSQL uses the operating system's filesystem for storage, while Oracle implements its own file system. In PostgreSQL...MSSQLTips.comTomas Vondra: Postgres vs. Linux filesystems (PGConf.EU 2023)Jan 9, 2024 — Postgres heavily relies on various parts of the operating system - filesystem and buffered i/O are good examples of such fundament...YouTubeShow allYou said: this is for a new setup we are just about to migrate and I believe we should move the base partitions database and microvms to lvm and keep the data on btrfsTo successfully execute this strategy, you must separate your storage layers carefully. Moving your microVM base partitions to LVM provides low-latency, raw block devices for your virtual disks (ideal for tools like Firecracker or QEMU/KVM). However, putting the PostgreSQL database data on Btrfs is highly discouraged due to severe performance bottlenecks.Btrfs uses a Copy-on-Write (CoW) design. When PostgreSQL frequently updates small chunks of data inside large files, Btrfs forces massive write amplification, fragmentation, and severe latency spikes.Here is the optimal architecture for your migration.1. Storage Architecture BreakdownLayer / ComponentTarget Storage EngineRationaleMicroVM Root DisksLVM Logical VolumesAllows passing fast, raw block devices directly to microVMs.MicroVM Base PartitionsLVM Logical VolumesNative, ultra-low latency snapshotting for instant VM cloning.PostgreSQL DataLVM + XFS (or ext4)Btrfs CoW destroys database performance. XFS handles heavy multi-threaded concurrent IOPS perfectly.Unstructured Data / FilesBtrfsExcellent choice for standard file storage, deduplication, and compressed assets.2. If You Must Use Btrfs for Postgres (Critical Tweaks)If your operational requirements strictly mandate that the PostgreSQL data directory resides on a Btrfs volume, you must disable Copy-on-Write for the database data folder before creating any files.Run these steps during your migration setup:Create the target directory:bashsudo mkdir -p /var/lib/postgresql/data
Use code with caution.Disable CoW attribute (NOCOW):bashsudo chattr +C /var/lib/postgresql/data
Use code with caution.Verify the flag:bashlsattr -d /var/lib/postgresql/data
# Output must show a capital 'C' (e.g., ---------------C-- /var/lib/postgresql/data)
Use code with caution.Note: Disabling CoW also disables Btrfs checksums and compression for that folder, essentially turning it into a basic filesystem layer.3. Step-by-Step Migration Setup BlueprintPhase A: Allocate the LVM Volume GroupAssuming you have a raw NVMe or SSD disk array at /dev/nvme0n1:bash# 1. Initialize physical volume
sudo pvcreate /dev/nvme0n1

# 2. Create the primary volume group
sudo vgcreate vg_infrastructure /dev/nvme0n1
Use code with caution.Phase B: Create Provisioning PoolsDivide your volume group to separate the infrastructure types:bash# 1. Allocate block for the Postgres optimized filesystem (e.g., 200GB)
sudo lvcreate -n lv_postgres_production -L 200G vg_infrastructure
sudo mkfs.xfs /dev/vg_infrastructure/lv_postgres_production

# 2. Allocate blocks for MicroVM base storage templates
sudo lvcreate -n lv_microvm_base_template -L 50G vg_infrastructure
Use code with caution.Phase C: Configure MicroVMs to Use LVM BlocksWhen launching your microVMs, do not pass virtual image files (like .qcow2). Instead, point your microVM configuration file directly to the raw LVM block path:json{
  "drive_id": "rootfs",
  "path_on_host": "/dev/vg_infrastructure/lv_microvm_base_template",
  "is_root_device": true,
  "is_read_only": false
}
Use code with caution.Which microVM hypervisor are you using (e.g., Firecracker, Cloud Hypervisor, or QEMU/KVM)? Knowing this will let me provide the exact block device configuration parameters for your VM orchestrator.3 sitesUnderstand Btrfs File System (Copy On Write, Sub-Volumes, Snapshots, Quota Group) — Part 2Nov 10, 2023 — Over time, it ( The Copy-On-Write (CoW) option ) can gradually slow down and fill up your Btrfs filesystem.MediumWhy are VMs not recommended on btrfs but they work fine on other Cow filesystems? : r/btrfsFeb 9, 2021 — You can disable CoW on a file-by-file basis under BtrFS ( btrfs wiki ) if you want. That can alleviate some of the performance iss...RedditUnderstand Btrfs File System (Copy On Write, Sub-Volumes, Snapshots, Quota Group) — Part 2Nov 10, 2023 — A crucial warning: Disabling CoW in Btrfs also turns off checksums, rendering Btrfs unable to detect corruption in nodatacow files...MediumYou said: no intention of btrfs for postgresql. backup is a dump. nixos.Understood. Moving the microVM blocks to LVM and separating your general data onto Btrfs makes perfect sense, while keeping PostgreSQL safely on a performance-tuned layer.Since you are running NixOS, managing raw LVM blocks, filesystems, and PostgreSQL configuration must be handled declaratively. NixOS manages the volume mapping, file-system mounting, and database initializations natively via your configuration.nix.Here is the declarative NixOS configuration to implement this architecture.1. Host Storage Configuration (hardware-configuration.nix)Add your filesystems and LVM logical volumes to your hardware configuration. This maps your optimized XFS partition for PostgreSQL and your storage partition for Btrfs.nix{ config, pkgs, ... }:

{
  # Ensure the LVM metadata daemon is enabled to discover volumes on boot
  services.lvm.enable = true;

  # Postgres Volume (Optimized XFS layer)
  fileSystems."/var/lib/postgresql" = {
    device = "/dev/vg_infrastructure/lv_postgres_production";
    fsType = "xfs";
    options = [ "defaults" "noatime" "nodiratime" "allocsize=64m" ];
  };

  # General Data Volume (Btrfs)
  fileSystems="/mnt/general-data" = {
    device = "/dev/vg_infrastructure/lv_general_data";
    fsType = "btrfs";
    options = [ "defaults" "compress=zstd" "noatime" ];
  };
}
Use code with caution.2. Declarative PostgreSQL Setup (configuration.nix)NixOS completely automates cluster initialization. Because /var/lib/postgresql is mounted via your LVM configuration above, NixOS will automatically initialize the cluster template inside that high-performance mountpoint with the correct permissions.nix{ config, pkgs, ... }:

{
  services.postgresql = {
    enable = true;
    # Ensure you pin the target version explicitly for migrations
    package = pkgs.postgresql_16; 
    
    # Tuning parameters for modern file system performance
    settings = {
      max_connections = 100;
      shared_buffers = "4GB";       # Adjust based on system RAM (25% total RAM)
      wal_sync_method = "fdatasync"; # Highly optimal for XFS backends
      synchronous_commit = "off";   # Turn off if you prioritize IOPS over strict instant safety
    };
  };
}
Use code with caution.3. Declarative Backup Engine (Dump-Based)Since you specified using database dumps for your backup strategy, use NixOS’s built-in automated service. This creates periodic gzip-compressed logical SQL dumps natively, bypassing file-system backup limitations completely.nix{ config, pkgs, ... }:

{
  services.postgresql.backup = {
    enable = true;
    # Store the daily dumps on your Btrfs volume to take advantage of zstd compression
    location = "/mnt/general-data/backups/postgresql"; 
    startAt = "*-*-* 02:00:00"; # Runs every night at 2:00 AM
    compression = "gzip";
    compressionLevel = 6;
  };
}
Use code with caution.MicroVM Block Device MappingFor your microVM base partitions, do not format them or add them to your fileSystems array. NixOS will ignore them on mount loops, leaving them as raw blocks. You can then pass the raw path /dev/vg_infrastructure/lv_microvm_base_template directly into your microVM nix runner configurations (like microvm.nix).Are you building your microVM architecture using the microvm.nix framework, or are you rolling custom QEMU/Firecracker systemd services? I can give you the exact Nix snippet to attach the raw LVM block as a drive.You said: microvm.nixWhen implementing microvm.nix on a NixOS host with an LVM backend, you must explicitly declare the raw block device inside the VM configuration's microvm.volumes attribute.Because microvm.nix runs under a dedicated, unprivileged system user (microvm), the host must also explicitly grant that user access to raw disk blocks.1. Host Configuration: Granting Disk PermissionsBy default, the host's microvm system user cannot interact with raw block files located in /dev/. You must add the user to the host's disk group inside your host-level configuration.nix:nix# host /etc/nixos/configuration.nix
{ config, pkgs, ... }:

{
  # Add the microvm daemon/user to the host's disk group
  users.users.microvm.extraGroups = [ "disk" ];
}
Use code with caution.2. MicroVM Definition: Attaching the Raw LVM BlockInside your declarative MicroVM specification (e.g., inside a Flake or your host's microvm.vms.<name>.config attribute), pass the raw block path directly into microvm.volumes.Specify the image as the path to your LVM logical volume block file, and set the type parameter to "block":nix# Inside your MicroVM definition block
{ config, lib, pkgs, ... }:

{
  networking.hostName = "database-worker-vm";

  microvm = {
    # Specify your hypervisor (e.g., cloud-hypervisor, qemu, firecracker)
    hypervisor = "cloud-hypervisor"; 
    
    # Configure the raw LVM allocation mapping
    volumes = [
      {
        # Path to the unformatted raw LVM volume on the host system
        image = "/dev/vg_infrastructure/lv_microvm_base_template";
        
        # Informs microvm.nix to pass this cleanly via virtio-block bypassing filesystem checks
        type = "block";
      }
    ];

    # Optional: If you want to use this passed block device as your persistent root file system
    # shares = [ {
    #   tag = "ro-store";
    #   proto = "virtiofs";
    #   source = "/nix/store";
    # } ];
    # writableStoreOverlay = true;
  };

  # Guest OS settings
  services.openssh.enable = true;
}
Use code with caution.How the Guest OS Sees the BlockWhen the microVM boots up using the configuration above:The hypervisor attaches the LVM logical volume via an optimized VirtIO block interface (virtio-blk).Inside the guest microVM instance, the raw block will present itself cleanly as a standard kernel block device, typically mapped to /dev/vda or /dev/vdb (depending on device boot ordering).You can safely run any inner-VM partitions, filesystems, or application layers directly against that virtual disk with native disk performance.Are you planning to configure this raw LVM block as the ephemeral root file system for the microVM, or will it be attached as a secondary persistent storage disk inside the guest?6 sitesNixCon2023 microvm.nixSep 9, 2023 — and I'm happy that these um diagnostics have now been taken over to TVIX and the new language server but that next can also delete...17mYouTube·NixConBlock device mount in guest (ZFS zvol) #273 - GitHubSep 10, 2024 — nixos = nixpkgs.lib.nixosSystem { inherit pkgs; specialArgs = { inherit inputs; }; modules = [ ./configuration.nix microvm.nixosMo...GitHubSops and MicroVM wiring - Help - NixOS DiscourseDec 31, 2025 — here is my nix host config: { config, self, lib, pkgs, ... }: { imports = [ self.inputs.microvm.nixosModules.host ]; microvm.vms =NixOS DiscourseShow allYou said: ephemeral rootTo use your raw LVM block as an ephemeral root filesystem in microvm.nix, you should use an overlay strategy.Because microvm.nix handles the Nix store using immutable references, the cleanest way to execute this is to pass your raw LVM block as the read-only underlying base storage, and then direct the guest OS to instantiate a tmpfs (RAM-backed) overlay on top. Every time the MicroVM reboots, all runtime root filesystem changes are discarded instantly, while the read performance hits the bare-metal raw LVM block.1. Format the LVM Block Device OnceEven though the root is ephemeral, microvm.nix requires the base disk to have a formatted structure so the init script can discover the guest's /nix/store contents or bootstrap data layout. Format the volume on the host just once using standard ext4:bashsudo mkfs.ext4 /dev/vg_infrastructure/lv_microvm_base_template
Use code with caution.2. The Declarative Guest MicroVM ConfigurationIn your microvm.nix configuration module for the guest, bind the raw LVM block as a block device. To make it truly ephemeral, enable writableStoreOverlay and tell the guest to mount its write-layer strictly into RAM:nix# Inside your microvm specification file (e.g. guest-vm.nix)
{ config, lib, pkgs, ... }:

{
  networking.hostName = "ephemeral-worker";

  microvm = {
    # Choose your optimized hypervisor interface
    hypervisor = "cloud-hypervisor"; 

    # Attach the raw LVM partition
    volumes = [
      {
        image = "/dev/vg_infrastructure/lv_microvm_base_template";
        type = "block";
      }
    ];

    # CRITICAL: Enables an ephemeral overlay filesystem
    # This prevents the hypervisor from committing writes back to your raw LVM block
    writableStoreOverlay = true;
  };

  # Direct the Guest OS kernel to drop all filesystem mutations into RAM
  fileSystems."/" = {
    device = "tmpfs";
    fsType = "tmpfs";
    # Limit size of runtime ephemeral modifications to avoid OOMing the guest RAM
    options = [ "mode=0755" "size=2G" ]; 
    neededForBoot = true;
  };

  # Standard minimal NixOS services
  services.openssh.enable = true;
}
Use code with caution.Why this design fits your setup:Zero Disk Bloat: Because changes live entirely in a tmpfs RAM layer, you do not need to orchestrate complex host-side LVM snapshot deletion scripts when a MicroVM dies.Instant Resets: A simple restart of the MicroVM systemd service completely purges the ephemeral runtime context, snapping the guest OS back to the clean base template defined in your nix files.No Read Penalty: The heavily utilized binaries (/nix/store) are read directly through the raw block device via virtio-blk paths, ensuring sub-second boot times.How to trigger a backup dump before destruction (Optional)Since you mentioned your fallback backup scheme relies entirely on a database dump, ensure your host orchestration service triggers the pg_dump sequence outside the microvm container context (e.g., streaming over network/sockets directly to your Btrfs partition /mnt/general-data/backups/) before stopping or killing the worker microVM context.Are you deploying these MicroVM nodes using NixOS Flakes with nixos-rebuild, or are you initiating them as individual systemd container services via microvm-run?Don't share a mutable rootfs with all microVMs #75 - GitHubJan 24, 2019 — nmeyerhans commented. ... Two immediate options come to mind for this, both of which are inspired by container (and/or live-cd) be...GitHubbureado/awesome-agent-runtime-security - GitHubPurpose-built NixOS for AI agents, support for QEMU/KVM with sub-3s boot, gVisor included for in-VM sandboxing, and vsock-based se...GitHubNixCon 2023microvm. nix. microvm.nix builds NixOS for a few virtual machine managers in a few different use-cases. Full virtualization suffer...2023.nixcon.orgcloud-hypervisor 50.x: raw disk autodetection disables sector 0 ...Feb 24, 2026 — cloud-hypervisor 50.x: raw disk autodetection disables sector 0 writes, breaking microvm writableStoreOverlay (/nix/.rw-store). En...github.comThe Linux Tier List - Page 2 - LearnLinuxTV CommunityJul 11, 2023 — I haven't delved too deep into it, but lately I've been dealing with microvm.nix. This allows you to run a minimal nixos VM using ...Learn Linux TV
