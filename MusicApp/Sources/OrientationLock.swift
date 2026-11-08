import SwiftUI
import UIKit

/// 视图方向锁定：全屏歌词强制横屏、主界面锁定竖屏。
///
/// Info.plist 已声明 Portrait + LandscapeLeft/Right；AppDelegate 按 FullScreenOrientation
/// 全局授权方向（比 per-View supportedInterfaceOrientations 更可靠），本控制器在出现时
/// 同步全局状态 + UIDevice KVC 触发实际旋转（dismiss 后主界面自动转回竖屏）。
struct OrientationLock: UIViewControllerRepresentable {
    /// 本视图允许的方向
    var mask: UIInterfaceOrientationMask
    /// 出现时强制旋转到的方向（nil = 只声明不强制）
    var force: UIInterfaceOrientation?

    func makeUIViewController(context: Context) -> UIViewController {
        OrientationVC(mask: mask, force: force)
    }

    func updateUIViewController(_ uiViewController: UIViewController, context: Context) {}

    private final class OrientationVC: UIViewController {
        let mask: UIInterfaceOrientationMask
        let force: UIInterfaceOrientation?

        init(mask: UIInterfaceOrientationMask, force: UIInterfaceOrientation?) {
            self.mask = mask
            self.force = force
            super.init(nibName: nil, bundle: nil)
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

        override var supportedInterfaceOrientations: UIInterfaceOrientationMask { mask }

        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            scheduleOrientationWork()
        }

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            scheduleOrientationWork()
        }

        /// ★ 2026-11-03：方向同步/强制旋转**必须跳出一个 runloop** 再执行。
        ///   viewWillAppear/viewDidAppear 可能发生在 SwiftUI 的 representable 更新/layout 事务内，
        ///   直接在回调里发布全局状态（@Published）或调 UIDevice 改方向，会触发
        ///   UIKit「Call must be made on main thread」断言 / 递归 layout 崩溃
        ///   （线上 1.1.20→1.1.36 反复出现的 signal 6，栈落在 SwiftUI layout）。
        private func scheduleOrientationWork() {
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.syncGlobalOrientation()
                self.forceOrientation()
            }
        }

        /// 把当前方向同步给全局策略（AppDelegate 依据它授权横屏/竖屏）
        private func syncGlobalOrientation() {
            if force == .landscapeRight || force == .landscapeLeft {
                FullScreenOrientation.shared.isFullScreen = true
            } else {
                FullScreenOrientation.shared.isFullScreen = false
            }
        }

        /// 强制旋转：呈现动画前后各触发一次 + 延迟再触发一次，规避系统方向重算时机问题；
        /// AppDelegate 已按 isFullScreen 授权方向，KVC 触发旋转即可被接受。
        private func forceOrientation() {
            guard let force else { return }
            setNeedsUpdateOfSupportedInterfaceOrientations()
            DispatchQueue.main.async {
                UIDevice.current.setValue(force.rawValue, forKey: "orientation")
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                UIDevice.current.setValue(force.rawValue, forKey: "orientation")
                self.setNeedsUpdateOfSupportedInterfaceOrientations()
            }
        }
    }
}

extension View {
    /// 包裹为强制横屏视图（全屏沉浸式歌词）
    func landscapeFullScreen() -> some View {
        OrientationLock(mask: .landscape, force: .landscapeRight)
            .overlay(self)
            .ignoresSafeArea()
    }

    /// 锁定竖屏（主界面 / 登录页，防止系统横屏授权被主界面接管）。
    /// 注意：不能加 .ignoresSafeArea()——它会把主界面安全区环境清空，
    /// 导致 GlobalTopBar 顶到状态栏后、底部 TabBar 沉到 Home 指示条后被挡住；
    /// 各页面背景自行铺满（内部已有 ignoresSafeArea），此处保持安全区完整。
    func portraitLocked() -> some View {
        OrientationLock(mask: .portrait, force: .portrait)
            .overlay(self)
    }
}
