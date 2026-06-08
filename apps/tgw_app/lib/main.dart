import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    const ProviderScope(
      child: TgwApp(),
    ),
  );
}

class TgwApp extends StatelessWidget {
  const TgwApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: "Trader Grim's Warehouse",
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const MainShell(),
    );
  }
}
