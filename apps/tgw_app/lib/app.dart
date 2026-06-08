import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'features/home/home_screen.dart';
import 'features/browse/browse_screen.dart';
import 'features/item/item_screen.dart';
import 'features/settings/settings_screen.dart';
import 'providers/providers.dart';

class MainShell extends ConsumerStatefulWidget {
  const MainShell({super.key});

  @override
  ConsumerState<MainShell> createState() => _MainShellState();
}

class _MainShellState extends ConsumerState<MainShell> {
  int _selectedIndex = 0;
  String? _selectedSku;

  void _onItemTapped(int index) {
    setState(() {
      _selectedIndex = index;
    });
  }

  void _openItem(String sku) {
    setState(() {
      _selectedSku = sku;
      _selectedIndex = 2; // Item tab
    });
  }

  @override
  Widget build(BuildContext context) {
    final connectionStatus = ref.watch(connectionStatusProvider);

    final List<Widget> screens = [
      const HomeScreen(),
      BrowseScreen(onItemTap: _openItem),
      ItemScreen(sku: _selectedSku),
      const SettingsScreen(),
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text("TGW"),
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(4.0),
          child: _ConnectionBanner(status: connectionStatus),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.read(connectionStatusProvider.notifier).checkConnection(),
          ),
        ],
      ),
      body: IndexedStack(
        index: _selectedIndex,
        children: screens,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: _onItemTapped,
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.grid_view), label: 'Browse'),
          NavigationDestination(icon: Icon(Icons.inventory_2), label: 'Item'),
          NavigationDestination(icon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}

class _ConnectionBanner extends StatelessWidget {
  final ConnectionStatus status;

  const _ConnectionBanner({required this.status});

  @override
  Widget build(BuildContext context) {
    Color color;
    String text;
    switch (status) {
      case ConnectionStatus.online:
        color = Colors.green;
        text = 'ONLINE';
        break;
      case ConnectionStatus.offline:
        color = Colors.orange;
        text = 'OFFLINE';
        break;
      case ConnectionStatus.error:
        color = Colors.red;
        text = 'ERROR';
        break;
    }

    return Container(
      color: color,
      height: 24,
      alignment: Alignment.center,
      child: Text(
        text,
        style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold),
      ),
    );
  }
}
